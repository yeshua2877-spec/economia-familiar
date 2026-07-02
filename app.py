import os
import json
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from anthropic import Anthropic
from dotenv import load_dotenv
import psycopg2

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-in-prod")
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

APP_PASSWORD = os.environ.get("APP_PASSWORD", "familia2026")
DATABASE_URL = os.environ.get("DATABASE_URL")

# Datos de ejemplo con los que se precarga la tabla la primera vez que se conecta a una DB vacía.
SEED_TRANSACTIONS = [
    (1,  "income",  "p1", "salario",      "💼", "Salario junio",       3500, "2026-06-01", False),
    (2,  "income",  "p2", "salario",      "💼", "Salario junio",       3000, "2026-06-01", False),
    (3,  "expense", "p1", "alimentacion", "🛒", "Supermercado",        320,  "2026-06-05", False),
    (4,  "expense", "p1", "ocio",         "🎮", "Netflix + Spotify",   45,   "2026-06-08", False),
    (5,  "expense", "p2", "transporte",   "🚗", "Gasolina semanal",    120,  "2026-06-10", False),
    (6,  "expense", "p1", "ocio",         "🎮", "Cena restaurante",    85,   "2026-06-14", True),
    (7,  "expense", "p2", "ropa",         "👕", "Ropa verano",         210,  "2026-06-16", True),
    (8,  "expense", "p1", "salud",        "🏥", "Farmacia",            45,   "2026-06-18", False),
    (9,  "income",  "p1", "freelance",    "💻", "Proyecto diseño web", 800,  "2026-06-20", False),
    (10, "expense", "p2", "ahorros",      "🏦", "Ahorro mensual",      180,  "2026-06-22", False),
]


def get_db():
    if not DATABASE_URL:
        raise RuntimeError(
            "Falta configurar DATABASE_URL (base de datos PostgreSQL) en las variables de entorno."
        )
    return psycopg2.connect(DATABASE_URL)


def init_db():
    if not DATABASE_URL:
        return
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id BIGSERIAL PRIMARY KEY,
                    type TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    cat TEXT NOT NULL,
                    icon TEXT,
                    description TEXT,
                    amount NUMERIC NOT NULL,
                    date DATE NOT NULL,
                    ai BOOLEAN DEFAULT FALSE
                )
            """)
            cur.execute("SELECT COUNT(*) FROM transactions")
            if cur.fetchone()[0] == 0:
                cur.executemany(
                    """INSERT INTO transactions (id, type, profile, cat, icon, description, amount, date, ai)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    SEED_TRANSACTIONS,
                )
                cur.execute(
                    "SELECT setval(pg_get_serial_sequence('transactions','id'), (SELECT MAX(id) FROM transactions))"
                )
        conn.commit()
    finally:
        conn.close()


init_db()


def row_to_tx(row):
    return {
        "id": row[0],
        "type": row[1],
        "profile": row[2],
        "cat": row[3],
        "icon": row[4],
        "desc": row[5],
        "amount": float(row[6]),
        "date": row[7].isoformat() if hasattr(row[7], "isoformat") else row[7],
        "ai": row[8],
    }


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == APP_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("index"))
        error = "Contraseña incorrecta"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/api/transactions", methods=["GET"])
@login_required
def get_transactions():
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, type, profile, cat, icon, description, amount, date, ai "
                "FROM transactions ORDER BY date, id"
            )
            rows = cur.fetchall()
        return jsonify([row_to_tx(r) for r in rows])
    finally:
        conn.close()


@app.route("/api/transactions", methods=["POST"])
@login_required
def create_transaction():
    data = request.json
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO transactions (type, profile, cat, icon, description, amount, date, ai)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id, type, profile, cat, icon, description, amount, date, ai""",
                (
                    data.get("type"),
                    data.get("profile"),
                    data.get("cat"),
                    data.get("icon"),
                    data.get("desc"),
                    data.get("amount"),
                    data.get("date"),
                    data.get("ai", False),
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return jsonify(row_to_tx(row))
    finally:
        conn.close()


@app.route("/analizar-gasto", methods=["POST"])
@login_required
def analizar_gasto():
    data = request.json
    gasto = data.get("gasto", {})
    ctx = data.get("contexto", {})

    prompt = f"""Eres un asesor financiero familiar. Analiza este gasto y decide si debes alertar al usuario.

GASTO NUEVO:
- Descripción: {gasto.get('desc')}
- Categoría: {gasto.get('cat')}
- Monto: ${gasto.get('amount')}
- Fecha: {gasto.get('date')}

CONTEXTO DEL MES:
- Total gastado en {gasto.get('cat')} este mes (incluyendo este gasto): ${ctx.get('total_mes_categoria', 0)}
- Presupuesto límite para {gasto.get('cat')}: ${ctx.get('presupuesto_limite', 'no definido')}
- Historial de gastos en esta categoría este mes: {ctx.get('historial_categoria', [])}

INSTRUCCIONES:
- Responde SOLO en JSON con este formato exacto: {{"alerta": true/false, "nivel": "warning"/"danger", "mensaje": "..."}}
- Genera alerta si: el gasto supera el 85% del presupuesto, es un gasto repetitivo innecesario, o el monto es excesivo para la categoría.
- Si no hay alerta, responde {{"alerta": false, "nivel": null, "mensaje": null}}
- El mensaje debe ser claro, breve (2-3 oraciones) y con recomendación concreta. Usa HTML <strong> para enfatizar datos clave.
- Tono: amigable pero directo."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    try:
        result = json.loads(message.content[0].text)
    except Exception:
        result = {"alerta": False, "nivel": None, "mensaje": None}

    return jsonify(result)


@app.route("/chat-asesor", methods=["POST"])
@login_required
def chat_asesor():
    data = request.json
    mensaje = data.get("mensaje", "")
    perfil = data.get("perfil", "p1")
    transactions = data.get("transactions", [])

    perfil_label = {"p1": "Persona 1", "p2": "Persona 2", "family": "Familia"}.get(perfil, "Usuario")

    ingresos = sum(t["amount"] for t in transactions if t["type"] == "income" and (perfil == "family" or t["profile"] == perfil))
    gastos = sum(t["amount"] for t in transactions if t["type"] == "expense" and (perfil == "family" or t["profile"] == perfil))
    balance = ingresos - gastos

    resumen = f"Ingresos: ${ingresos} | Gastos: ${gastos} | Balance: ${balance}"

    prompt = f"""Eres un asesor financiero familiar, empático y práctico. Ayudas a la familia a optimizar su economía.

PERFIL ACTIVO: {perfil_label}
RESUMEN FINANCIERO DEL MES: {resumen}
TRANSACCIONES RECIENTES: {[{'desc': t['desc'], 'cat': t['cat'], 'amount': t['amount'], 'type': t['type']} for t in transactions[-10:]]}

PREGUNTA DEL USUARIO: {mensaje}

Responde en español, de forma concisa (máximo 4 oraciones), con datos concretos del contexto financiero real. Puedes usar <strong> para resaltar números clave. Sé directo y útil."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )

    return jsonify({"respuesta": message.content[0].text})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
