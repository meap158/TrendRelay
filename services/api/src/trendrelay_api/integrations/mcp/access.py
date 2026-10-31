"""Authorization for the workspace capability served by the local MCP process.

The connection acts for the local operator. It does not mint a user session or
claim multi-factor assurance: installations requiring AAL2 refuse governed
operations until a delegated authentication mechanism is available.
"""

from sqlalchemy.orm import Session

from trendrelay_api.auth import LOCAL_ADMIN_ID, CurrentUser, require_governed_assurance
from trendrelay_api.foundation import membership, require_role


def require_access(
    session: Session, workspace_id: str, roles: set[str], *, governed: bool = False
) -> CurrentUser:
    actor = CurrentUser(id=LOCAL_ADMIN_ID, local_development=True)
    require_role(membership(session, workspace_id, actor.id), roles)
    if governed:
        require_governed_assurance(actor)
    return actor


def confirm_external_action(confirmed: bool) -> None:
    if confirmed is not True:
        raise PermissionError(
            "This action requires confirm_external_action=true after user consent."
        )
