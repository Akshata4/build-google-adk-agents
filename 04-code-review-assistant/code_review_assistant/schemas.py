from pydantic import BaseModel, Field


class TestSuite(BaseModel):
    intended_behavior: str = Field(
        description="One or two sentences: what the code is meant to do."
    )
    test_code: str = Field(
        description=(
            "Python source containing only test_* functions (plus small "
            "helpers). No imports of the code under test, no prints."
        )
    )
