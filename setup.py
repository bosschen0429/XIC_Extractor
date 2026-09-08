"""Build the optional native retained-receipt encoder for installed packages."""

from Cython.Build import cythonize
from setuptools import Extension, setup

setup(
    ext_modules=cythonize(
        [
            Extension(
                "xic_extractor.alignment._completion_receipt_native",
                ["xic_extractor/alignment/_completion_receipt_native.pyx"],
                language="c++",
                optional=True,
            )
        ],
        build_dir="build/cython",
        compiler_directives={"language_level": 3},
    )
)
