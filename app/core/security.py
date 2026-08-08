from fastapi import HTTPException, Request


LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def require_local_operator(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    if client_host not in LOCAL_HOSTS:
        raise HTTPException(status_code=403, detail="Local operator access required")
