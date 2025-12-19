"""This module contains the Pydantic schemas for utilities."""

# Standard Library
from typing import Optional

# Third Party Library
from pydantic import BaseModel, ConfigDict, Field


class TranslationIn(BaseModel):
    """Pydantic model for translation input."""

    key: str = Field(
        ..., description="Stable identifier for the field being translated."
    )
    source_language_hint: Optional[str] = Field(
        default=None,
        description="BCP-47 language hint from extraction (may be 'und').",
    )
    text: str = Field(..., description="Original text to translate.")

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class TranslationOut(BaseModel):
    """Pydantic model for translation output."""

    confidence: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Optional translation confidence."
    )
    key: str
    detected_source_language: str = Field(
        ...,
        description="Detected BCP-47 language code of the input (e.g., 'en', 'sw').",
    )
    translated_en: str

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class TranslationBatch(BaseModel):
    """Pydantic model for a batch of translations."""

    items: list[TranslationOut]

    model_config = ConfigDict(extra="forbid", from_attributes=True)
