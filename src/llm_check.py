import argparse
import asyncio
import os

from src.llm import get_llm_client


async def _run(prompt: str) -> None:
    llm = get_llm_client()
    response = await llm.chat("Ты тестовый агент.", prompt)
    print(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini connectivity check")
    parser.add_argument("--prompt", default="Ответь одним словом: ping")
    parser.add_argument("--provider", choices=["gemini", "mistral"])
    parser.add_argument("--model")
    args = parser.parse_args()
    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        if args.provider == "mistral":
            os.environ["MISTRAL_MODEL"] = args.model
        elif args.provider == "gemini":
            os.environ["GEMINI_MODEL"] = args.model
    asyncio.run(_run(args.prompt))


if __name__ == "__main__":
    main()
