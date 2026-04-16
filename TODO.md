- rendered_code_path should be normalised, currently it's printed as:
'''
generated code folder:
/Users/nejcstebe/codeplain/codeplain/a/b/plain_modules/../../b/hello_world_python/
'''
- should find a way not to alter the test scripts
- there are two identical directories in the plain_modules/
│   └── plain_modules
│       ├── hello_world_python
│       │   ├── hello_world.py
│       │   └── tests
│       │       ├── __init__.py
│       │       └── test_hello.py
│       └── python_hello_world_python
│           ├── hello_world.py
│           └── tests
│               ├── __init__.py
│               └── test_hello.py
- figure out it the paths from the CLI args should be relative to spec file too