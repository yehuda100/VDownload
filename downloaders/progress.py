from typing import Protocol, runtime_checkable


@runtime_checkable
class ProgressReporter(Protocol):
    async def report(self, message: str) -> None: ...
