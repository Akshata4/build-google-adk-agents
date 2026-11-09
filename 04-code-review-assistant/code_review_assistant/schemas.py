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


class CodeFix(BaseModel):
    fixed_code: str = Field(
        description="The whole fixed module, ready to run. Plain code, no fences."
    )
    changes: list[str] = Field(
        description="One short sentence per change, citing the original line."
    )
    disputed_tests: list[str] = Field(
        default_factory=list,
        description=(
            "Tests whose expectation looks wrong, each as 'test_name: reason'. "
            "Empty if every test is right."
        ),
    )
