import asyncio
import os
from dotenv import load_dotenv
from browser_use import Agent
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import ConfigDict

class CustomChatOpenAI(ChatOpenAI):
    model_config = ConfigDict(extra="allow")

    @property
    def provider(self):
        return "openai"

async def main():
    load_dotenv()
    llm = CustomChatOpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY_1") or os.environ.get("GROQ_API_KEY_2"),
        model="llama-3.3-70b-versatile",
    )
    
    agent = Agent(
        task="Go to https://www.saucedemo.com/ and print the page title.",
        llm=llm
    )
    
    result = await agent.run()
    print("\n--- Result ---")
    try:
        print(result)
    except Exception as e:
        print(f"Print error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
