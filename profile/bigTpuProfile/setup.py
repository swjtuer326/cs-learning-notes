from setuptools import setup, find_packages

setup(
    name="bigTpuProfile",
    use_scm_version=True,
    packages=find_packages(include=["bigTpuProfile", "bigTpuProfile.*"]),
    description="A profile visualization tool for AKS series",
    long_description=open('README.md', encoding='utf-8').read(),
    long_description_content_type="text/markdown",
    author="shaoxiong.xiang",
    author_email="shaoxiong.xiang@icloud.com",
    url="",
    install_requires=[
        'pandas',
        'bs4',
        'xlsxwriter',
        'numpy>=1.25,<2.4',
        'tqdm',
        'perfetto',
        'protobuf>=6.31.1'
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.7',
    entry_points={
        'console_scripts': [
            'bigTpuProfile=bigTpuProfile.main:main',
        ],
    },
    include_package_data=True,
)
