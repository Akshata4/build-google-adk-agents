# Step 1 placeholder: a tool-less agent used only to verify the install and
# Gemini credentials. Tools are added in later steps.
from dotenv import load_dotenv
from google.adk.agents import Agent

load_dotenv()

root_agent = Agent(
    model="gemini-2.5-flash",
    name="software_assistant",
    instruction="You are a helpful software bug assistant.",
)
