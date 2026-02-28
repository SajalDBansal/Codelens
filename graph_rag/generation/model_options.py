"""Avoid unsupported sampling parameters when a reasoning model is selected."""


def sampling_options(model: str) -> dict:
    # Custom/new reasoning models use their defaults. Do not guess capabilities
    # from the models-list endpoint, which only returns model identity metadata.
    if model.startswith(("gpt-4.1", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo")):
        return {"temperature": 0}
    return {}
