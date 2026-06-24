import os
from flask import Flask, render_template, request, jsonify
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/analizar-gasto", methods=["POST"])
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

    import json
    try:
        result = json.loads(message.content[0].text)
    except Exception:
        result = {"alerta": False, "nivel": None, "mensaje": None}

    return jsonify(result)


@app.route("/chat-asesor", methods=["POST"])
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
