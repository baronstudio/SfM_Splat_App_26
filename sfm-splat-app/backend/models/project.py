import json
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel


class Project(SQLModel, table=True):
    id: Optional[str] = Field(
        default_factory=lambda: uuid4().hex[:8], primary_key=True
    )
    name: str
    slug: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    current_step: int = 0
    step_status: str = "{}"
    input_video_path: Optional[str] = None
    frame_count: int = 0
    # Capture metadata, typed by the user and read by nothing in the pipeline:
    # how the footage was shot, and what the project is. Columns rather than
    # keys in `settings_json`, which §4 reserves for per-step overrides of a
    # `defaults.json` section — these have no default anything could inherit,
    # and the project list draws the author without parsing a blob.
    footage_author: Optional[str] = None
    description: Optional[str] = None
    settings_json: str = "{}"
    error_message: Optional[str] = None
    # Archived: the files live in a .zip under projects/_archives/ and the row
    # stays in the list, read-only, until it is restored (CLAUDE.md §14).
    archived_at: Optional[datetime] = None
    archive_path: Optional[str] = None

    def get_step_status(self) -> dict:
        return json.loads(self.step_status)

    def set_step_status(self, status: dict) -> None:
        self.step_status = json.dumps(status)
