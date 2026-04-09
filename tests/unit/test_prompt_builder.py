from app.domain.services.prompt_builder import PromptBuilder


def test_prompt_builder_contains_history():
    builder = PromptBuilder()
    prompt = builder.build_chat_prompt(
        [
            {"role": "user", "content": "hola"},
            {"role": "assistant", "content": "qué tal"},
        ]
    )

    assert "Historial:" in prompt
    assert "user: hola" in prompt
    assert "assistant: qué tal" in prompt
