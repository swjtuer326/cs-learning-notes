from setuptools import setup, find_packages

setup(
    name="bigTpuProfile",
    version="0.1",
    packages=find_packages(),
    description="A profile visualization tool for AKS series",
    long_description=open('README.md').read(),
    long_description_content_type="text/markdown",
    author="shaoxiong.xiang",
    author_email="shaoxiong.xiang@bigtpu.com",
    url="",
    install_requires=[
        'xlsxwriter'
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
    entry_points={
        'console_scripts': [
            'bigTpuProfile=bigTpuProfile.main:main',
        ],
    },
    include_package_data=True,
    package_data={
        'bigTpuProfile': ['PerfAI/**/*'],
    },
)
