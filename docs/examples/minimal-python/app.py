from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "Hello from Kata minimal Python example!"}
