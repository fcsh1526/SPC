"""Control plans (draft 6.7) and the seven SPC roles with their competencies (draft 6.8). Routes under /api/plans, /api/people, /api/spc-roles."""

from __future__ import annotations

from fastapi import Depends, FastAPI

from spc.api.errors import ApiError
from spc.api.schemas import CompetenceBody, PeopleRolesBody, PlanApproveBody, PlanBody, ReasonBody
from spc.auth import User
from spc.plan import roles as R
from spc.plan.service import PeopleService, PlanService


def add_plan_routes(app: FastAPI, plans: PlanService, people: PeopleService, reader, engineer, admin) -> None:
    @app.get("/api/spc-roles")
    def spc_roles(_: User = Depends(reader)):
        """Table 6-1 of the draft: the roles, the competences and the level each role needs."""
        return {"roles": list(R.ROLES), "competences": list(R.COMPETENCES), "matrix": R.MATRIX, "approvers": list(R.APPROVERS), "login_suggestion": R.LOGIN_SUGGESTION,
                "staffing": people.staffing()}

    @app.get("/api/people")
    def list_people(_: User = Depends(engineer)):
        return {"people": people.list()}

    @app.get("/api/people/{user_id}")
    def get_person(user_id: int, user: User = Depends(reader)):
        if user.id != user_id and not user.can("engineer"):  # the competences of a person are not for everybody
            raise ApiError(403, "forbidden", "this needs the engineer role", role="engineer")
        return people.get(user_id)

    @app.put("/api/people/{user_id}/roles")
    def set_roles(user_id: int, body: PeopleRolesBody, user: User = Depends(engineer)):
        return people.set_roles(user_id, body.roles, user)

    @app.post("/api/people/{user_id}/competences")
    def record_competence(user_id: int, body: CompetenceBody, user: User = Depends(engineer)):
        return people.record_competence(user_id, body.competence, body.level, body.date, body.note, user)

    @app.get("/api/plans")
    def list_plans(_: User = Depends(reader)):
        return {"plans": plans.list()}

    @app.post("/api/plans")
    def create_plan(body: PlanBody, user: User = Depends(engineer)):
        return plans.create(body.record, user)

    @app.get("/api/plans/{pid}")
    def get_plan(pid: int, _: User = Depends(reader)):
        return plans.view(pid)

    @app.put("/api/plans/{pid}")
    def update_plan(pid: int, body: PlanBody, user: User = Depends(engineer)):
        return plans.update(pid, body.record, user)

    @app.post("/api/plans/{pid}/approve")
    def approve_plan(pid: int, body: PlanApproveBody, user: User = Depends(engineer)):
        return plans.approve(pid, body.role, body.note, user)

    @app.post("/api/plans/{pid}/release")
    def release_plan(pid: int, body: ReasonBody, user: User = Depends(engineer)):
        return plans.release(pid, body.reason, user)

    @app.post("/api/plans/{pid}/withdraw")
    def withdraw_plan(pid: int, body: ReasonBody, user: User = Depends(engineer)):
        return plans.withdraw(pid, body.reason, user)

    @app.delete("/api/plans/{pid}")
    def delete_plan(pid: int, user: User = Depends(admin)):
        plans.delete(pid, user)
        return {"ok": True}
