import os
import json
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage


chat_model = ChatOllama(
    model="KubeAssist",
    base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434")
)


def ask_kubeassist(user_prompt: str, context_data: dict = None) -> str:
    """
    Send a prompt to KubeAssist model.
    Optionally pass live cluster data as context.
    """
    if context_data:
        context_str = json.dumps(context_data, indent=2)
        content = f"Here is the current cluster data:\n{context_str}\n\n{user_prompt}"
    else:
        content = user_prompt

    response = chat_model.invoke([HumanMessage(content=content)])
    return response.content