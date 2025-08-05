def analyze_log(data):
    log = data.get("log")
    # Aquí normalmente llamarías al LLM, pero lo simulamos:
    return {
        "error_type": "NullPointerException",
        "create_ticket": True,
        "ticket_title": "Error en RiskPlanService.java",
        "ticket_description": f"Se detectó un NullPointerException en el log: {log}"
    }