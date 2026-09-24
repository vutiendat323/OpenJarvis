"""Model IDs at the OpenRouter API boundary."""


def openrouter_model_id(model: str) -> str:
    """Remove the app routing prefix while preserving OpenRouter-owned IDs."""
    provider_model = model.removeprefix("openrouter/")
    if "/" in provider_model:
        return provider_model
    return model
