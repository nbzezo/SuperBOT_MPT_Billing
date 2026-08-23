import asyncio
from state import get_logs

async def main():
    logs = await get_logs(10)
    for l in logs:
        print(f"[{l['code']}] {l['msg']}")

asyncio.run(main())
