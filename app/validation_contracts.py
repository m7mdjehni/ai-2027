"""Phase 1 request contract: no executable, image, profile, path, or timeout inputs."""
from pydantic import BaseModel, Field


class ValidateProject(BaseModel):
    revision: str = Field(..., regex=r"^[a-f0-9]{64}$")

    class Config:
        extra = "forbid"


class HostValidateProject(ValidateProject):
    project_id: str = Field(..., regex=r"^[a-f0-9]{32}$")


def check_trusted_result(result, project_id, revision):
    """Validate the authenticated host's completed contract before persistence.

    In particular, neither profile nor cleanup confirmation has a success default.
    This is not an endpoint for accepting caller-supplied validation evidence.
    """
    statuses = {'passed', 'failed', 'stale', 'timeout', 'infrastructure_error'}
    if (not isinstance(result, dict) or result.get('schema_version') != 1
            or result.get('project_id') != project_id or result.get('revision') != revision
            or result.get('profile') != 'static-web-v1'
            or type(result.get('passed')) is not bool
            or type(result.get('cleanup_confirmed')) is not bool
            or not isinstance(result.get('status'), str)
            or result.get('status') not in statuses
            or result['passed'] != (result['status'] == 'passed')
            or not isinstance(result.get('errors'), list) or len(result['errors']) > 100
            or (result['passed'] and (result['errors'] or result.get('current_revision') != revision))
            or type(result.get('duration_ms')) is not int or not 0 <= result['duration_ms'] <= 600000):
        raise ValueError('Invalid trusted validation response')
    for error in result['errors']:
        if (not isinstance(error, dict)
                or not isinstance(error.get('file'), str) or len(error['file']) > 240
                or any(not isinstance(error.get(key), str) or len(error[key]) > 200
                       for key in ('check', 'code'))
                or any(type(error.get(key)) is not int or not 1 <= error[key] <= 1000000
                       for key in ('line', 'column'))):
            raise ValueError('Invalid trusted validation diagnostics')
    return result
