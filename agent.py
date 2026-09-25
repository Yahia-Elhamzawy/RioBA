import asyncio
from dotenv import load_dotenv
import os

load_dotenv()

from langchain_openai import ChatOpenAI
from browser_use import Agent

from pydantic import ConfigDict

class CustomChatOpenAI(ChatOpenAI):
    model_config = ConfigDict(extra="allow")

    @property
    def provider(self):
        return "openai"

async def main():
    print("Starting the Web QA Agent...")
    
    # Initialize the model using OpenAI compatible endpoint for Groq
    llm = CustomChatOpenAI(
        base_url="https://api.groq.com/openai/v1",
        api_key=os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY_1") or os.environ.get("GROQ_API_KEY_2"),
        model="llama-3.3-70b-versatile",
    )
    
    # Define the QA task. Using a standard testing site.
    task_description = (
        "Go to https://www.saucedemo.com/ "
        "Use the username 'standard_user' and password 'secret_sauce' to login. "
        "After logging in, add the first product you see to the cart. "
        "Then click on the cart icon to view the cart. "
        "Finally, click on the checkout button. "
        "If you encounter any issues, describe them."
    )
    
    print(f"Task: {task_description}")
    print("Opening browser... (This might take a moment)")
    
    # Create the agent
    agent = Agent(
        task=task_description,
        llm=llm,
    )
    
    # Run the agent
    result = await agent.run()
    
    print("\n--- Final Result ---")
    try:
        print(result)
    except UnicodeEncodeError:
        print("Result contained unicode characters that couldn't be printed directly.")
        print(str(result).encode('utf-8', 'ignore').decode('utf-8'))

if __name__ == "__main__":
    asyncio.run(main())
