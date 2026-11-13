from fastapi import FastAPI, Request
import uvicorn
import os

app = FastAPI()

# Сюда позже вставим логику вызова Менеджера карточек
GROK_AGENT_ID = os.getenv("GROK_AGENT_ID", "")

@app.post("/grok-webhook")
async def grok_webhook(request: Request):
    data = await request.json()
    request_id = data.get("request_id")
    user_data = data.get("data", {})

    print(f"Получена заявка #{request_id}")
    print(user_data)

    # === ЗДЕСЬ БУДЕТ ВЫЗОВ МЕНЕДЖЕРА КАРТОЧЕК ===
    # Пока просто подтверждаем получение
    # Когда будет понятно, как дергать Grok Bot из кода — вставим сюда

    return {"status": "ok", "request_id": request_id}

@app.get("/")
async def root():
    return {"message": "Grok webhook service is running"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
