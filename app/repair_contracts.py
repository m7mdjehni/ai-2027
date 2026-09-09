"""Only managed identity and optimistic revision are accepted, never commands."""
from pydantic import Field
from app.validation_contracts import ValidateProject, HostValidateProject


class RepairProject(ValidateProject):
    run_id: str = Field(..., regex=r'^[a-f0-9]{32}$')


class HostRepairProject(HostValidateProject):
    run_id: str = Field(..., regex=r'^[a-f0-9]{32}$')
