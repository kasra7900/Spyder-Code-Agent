from setuptools import setup, find_packages

setup(
    name="spyder-code-agent",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "spyder.plugins": [
            "code_agent = spyder_code_agent.plugin:CodeAgent"
        ]
    },
    install_requires=["spyder>=6.0", "openai"],
)