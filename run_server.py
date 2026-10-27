import os

import uvicorn

if __name__ == "__main__":
    # Localhost by default: the API is unauthenticated unless API_KEY is set.
    host = os.getenv("AGENTOS_HOST", "127.0.0.1")
    uvicorn.run("backend.api.main:app", host=host, port=8000, reload=True)
