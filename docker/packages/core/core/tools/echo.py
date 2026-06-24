from typing import Annotated

from pydantic import Field


async def echo(message: Annotated[str, Field(description="The message to echo")]) -> str:
    """Echoes a message"""
    return message + message
