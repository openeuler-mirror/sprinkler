import setuptools

__description__ = 'A tool for environmental detection and positioning'
__version__ = '1.0'
__author__ = 'Lingqu'
__url__ = 'https://codehub-y.huawei.com/LingquPDU/SE/Sprinkler/'
__keywords__ = 'Lingqu, Tools'
__license__ = 'https://codehub-y.huawei.com/LingquPDU/SE/Sprinkler/'
__package_name__ = 'sprinkler'

setuptools.setup(
    name=__package_name__,
    # Versions should comply with PEP440.  For a discussion on single-sourcing
    # the version across setup.py and the project code, see
    # https://packaging.python.org/en/latest/single_source_version.html
    version=__version__,
    description=__description__,
    # The project's main homepage.
    url=__url__,
    author=__author__,
    # The licence under which the project is released
    license=__license__,
    classifiers=[
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'Intended Audience :: Information Technology',
        # Indicate what your project relates to
        'Topic :: Scientific/Engineering :: Artificial Intelligence',
        'Topic :: Software Development :: Libraries :: Python Modules',
        # Supported python versions
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        # Additional Setting
        'Environment :: Console',
        'Natural Language :: English',
        'Operating System :: OS Independent',
    ],
    install_requires=[
        "fabric>=3.0",
        "PyYAML>=6.0",
        "pandas==2.3.3",
        "pexpect",
        "argcomplete",
        "openpyxl",
        "prompt_toolkit",
        "paramiko>=2.10",
        "telnetlib3>=2.0.0",
        "streamlit>=1.30",
    ],
    python_requires='>=3.6',
    packages=setuptools.find_packages(),
    # Add in any packaged data.
    exclude_package_data={'': ['**/*.md']},
    package_data={},
    zip_safe=False,
    # PyPI package information.
    keywords=__keywords__,
    cmdclass={},
    entry_points={
        "console_scripts": [
            "sprinkler = sprinkler.run:main", #智算入口
        ]
    },
    include_package_data=True,
    ext_modules=[]
)
