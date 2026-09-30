"""The query layer's own Gemini response schema (design section 5 step 1): the SQL statement
Gemini wrote, plus one sentence on what it answers. Kept apart from `app.agents.schema`
because this belongs to a different agent (`query`), with nothing in common with the
Extractor's or Validator's schemas.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SqlAnswer(BaseModel):
    sql: str = Field(description="Exactly one read-only SELECT statement, no trailing text.")
    explanation: str = Field(description="One sentence on what the SQL answers.")
