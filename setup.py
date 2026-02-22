from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="tglib",
    version="0.1.0",
    author="Ankit Chaubey",
    author_email="ankitchaubey.dev@gmail.com",
    description="An experimental full MTProto Python client library",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/ankit-chaubey/TGLib",
    project_urls={
        "Bug Tracker": "https://github.com/ankit-chaubey/TGLib/issues",
        "Source Code": "https://github.com/ankit-chaubey/TGLib",
    },
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Communications :: Chat",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.10",
    install_requires=[
        "pyaes",
        "pycryptodome",
        "aiosqlite",
    ],
    keywords=["telegram", "mtproto", "tglib", "client", "api", "bot", "async"],
)
