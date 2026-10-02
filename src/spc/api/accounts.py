"""Login, logout, password change, user management and the audit trail."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Query, Request, Response

from spc.api.schemas import LoginBody, NewUserBody, PasswordBody, ResetPasswordBody, UpdateUserBody
from spc.auth import Audit, AuthService, User

COOKIE = "spc_session"


def add_account_routes(app: FastAPI, auth: AuthService, audit: Audit, admin, secure_cookies: bool | None) -> None:
    def set_cookie(request: Request, response: Response, token: str) -> None:
        secure = request.url.scheme == "https" if secure_cookies is None else secure_cookies
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=secure, path="/")

    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request, response: Response):
        user = auth.authenticate(body.username, body.password)
        auth.revoke(request.cookies.get(COOKIE))  # a login never reuses an older session
        token, csrf = auth.create_session(user)
        set_cookie(request, response, token)
        return {"user": user.to_json(), "csrf": csrf}

    @app.get("/api/auth/me")
    def me(request: Request):
        info = request.state.session
        return {"user": info.user.to_json(), "csrf": info.csrf}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        auth.revoke(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.post("/api/auth/password")
    def change_password(body: PasswordBody, request: Request):
        info = request.state.session
        user = auth.change_password(info.user, body.current, body.new, keep=info.token_hash)
        return {"user": user.to_json()}

    # ------------------------------------------------------------------ administration

    @app.get("/api/users")
    def list_users(_: User = Depends(admin)):
        return {"users": [u.to_json() for u in auth.list_users()]}

    @app.post("/api/users")
    def create_user(body: NewUserBody, actor: User = Depends(admin)):
        user = auth.create_user(body.username, body.password, body.role, body.display_name,
                                must_change=body.must_change, actor=actor)
        return {"user": user.to_json()}

    @app.patch("/api/users/{user_id}")
    def update_user(user_id: int, body: UpdateUserBody, actor: User = Depends(admin)):
        user = auth.update_user(user_id, role=body.role, active=body.active, display_name=body.display_name, actor=actor)
        return {"user": user.to_json()}

    @app.post("/api/users/{user_id}/password")
    def reset_password(user_id: int, body: ResetPasswordBody, actor: User = Depends(admin)):
        user = auth.reset_password(user_id, body.password, must_change=body.must_change, actor=actor)
        return {"user": user.to_json()}

    @app.post("/api/users/{user_id}/unlock")
    def unlock(user_id: int, actor: User = Depends(admin)):
        user = auth.get_user(user_id)
        auth.unlock(user.username)
        audit.append("user_unlocked", user_id=actor.id, username=actor.username, target=user.username)
        return {"user": user.to_json()}

    @app.get("/api/audit")
    def audit_list(limit: int = Query(100, ge=1, le=500), before: int | None = Query(None, ge=1), _: User = Depends(admin)):
        return {"entries": audit.list(limit, before)}

    @app.get("/api/audit/verify")
    def audit_verify(_: User = Depends(admin)):
        return audit.verify()
