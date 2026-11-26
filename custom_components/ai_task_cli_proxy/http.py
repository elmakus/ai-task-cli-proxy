"""Bounded HTTP body reads shared by configuration and generation."""

from aiohttp import StreamReader


class ResponseTooLarge(ValueError):
    """A response exceeded the configured byte budget."""


async def async_read_bounded(stream: StreamReader, limit: int) -> bytearray:
    """Read to EOF or reject the first byte beyond the limit."""
    body = bytearray()
    while chunk := await stream.read(min(65536, limit - len(body) + 1)):
        if len(body) + len(chunk) > limit:
            raise ResponseTooLarge("Response exceeds size limit")
        body.extend(chunk)
    return body
