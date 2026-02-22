from setuptools import setup, find_packages

setup(
    name="tglib",
    version="0.1.0",
    description="A full MTProto Python client library",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "pyaes",
        "pycryptodome",
        "aiosqlite",
    ],
)
