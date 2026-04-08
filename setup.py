#!/usr/bin/env python3
#
# Copyright (c)  2022  Xiaomi Corporation (author: Wei Kang)

import glob
import os
import re
import shutil
import subprocess
import sys

import setuptools
from setuptools.command.build_ext import build_ext

cur_dir = os.path.dirname(os.path.abspath(__file__))


def cmake_extension(name, *args, **kwargs) -> setuptools.Extension:
    kwargs["language"] = "c++"
    sources = []
    return setuptools.Extension(name, sources, *args, **kwargs)


class BuildExtension(build_ext):
    def build_extension(self, ext: setuptools.extension.Extension):
        # build/temp.linux-x86_64-3.8
        build_dir = self.build_temp
        os.makedirs(build_dir, exist_ok=True)

        # build/lib.linux-x86_64-3.8
        os.makedirs(self.build_lib, exist_ok=True)

        ft_dir = os.path.dirname(os.path.abspath(__file__))

        cmake_args = os.environ.get("FT_CMAKE_ARGS", "")
        make_args = os.environ.get("FT_MAKE_ARGS", "")
        system_make_args = os.environ.get("MAKEFLAGS", "")

        if cmake_args == "":
            cmake_args = "-DCMAKE_BUILD_TYPE=Release -DFT_BUILD_TESTS=OFF"

        if make_args == "" and system_make_args == "" and os.name != "nt":
            make_args = " -j "

        if "PYTHON_EXECUTABLE" not in cmake_args:
            print(f"Setting PYTHON_EXECUTABLE to {sys.executable}")
            cmake_args += f" -DPYTHON_EXECUTABLE={sys.executable}"

        config = "Debug" if self.debug else "Release"
        cmake_cmd = ["cmake"] + cmake_args.split() + [ft_dir]
        print(f"Running configure command: {' '.join(cmake_cmd)}")

        try:
            subprocess.check_call(cmake_cmd, cwd=self.build_temp)

            build_cmd = ["cmake", "--build", ".", "--target", "_fast_rnnt"]
            if os.name == "nt":
                # Multi-config generators (e.g. Visual Studio) need --config.
                build_cmd.extend(["--config", config])

            if make_args != "":
                build_cmd.extend(["--", *make_args.split()])

            print(f"Running build command: {' '.join(build_cmd)}")
            subprocess.check_call(build_cmd, cwd=self.build_temp)
        except subprocess.CalledProcessError as e:
            raise Exception(
                "\nBuild fast_rnnt failed. Please check the error "
                "message.\n"
                "You can ask for help by creating an issue on GitHub.\n"
                "\nClick:\n"
                "\thttps://github.com/danpovey/fast_rnnt/issues/new\n"  # noqa
            ) from e

        # Copy generated extension module into wheel lib dir.
        patterns = ["*.so", "*.so.*", "*.dylib", "*.pyd", "*.dll"]
        copied = False
        for pattern in patterns:
            for lib in glob.glob(f"{build_dir}/lib/{pattern}"):
                if "_fast_rnnt" not in os.path.basename(lib):
                    continue
                print(f"Copying {lib} to {self.build_lib}/")
                shutil.copy(lib, self.build_lib)
                copied = True

        if not copied:
            candidates = []
            for pattern in patterns:
                candidates.extend(glob.glob(f"{build_dir}/**/{pattern}", recursive=True))

            for lib in candidates:
                if "_fast_rnnt" not in os.path.basename(lib):
                    continue
                print(f"Copying {lib} to {self.build_lib}/")
                shutil.copy(lib, self.build_lib)
                copied = True

        if not copied:
            raise RuntimeError(
                "Failed to locate built extension _fast_rnnt in build directory."
            )

        # Write _version.py into the build directory (not source tree)
        version = get_package_version()
        version_suffix = os.environ.get("FT_VERSION_SUFFIX", "").strip()
        full_version = version + version_suffix
        pkg_build_dir = os.path.join(self.build_lib, package_name)
        os.makedirs(pkg_build_dir, exist_ok=True)
        version_file = os.path.join(pkg_build_dir, "_version.py")
        with open(version_file, "w") as f:
            f.write(f'__version__ = "{full_version}"\n')


def read_long_description():
    with open("README.md", encoding="utf8") as f:
        readme = f.read()
    return readme


def get_package_version():
    override = os.environ.get("FT_VERSION_OVERRIDE", "").strip()
    if override:
        return override

    with open("CMakeLists.txt") as f:
        content = f.read()

    latest_version = re.search(r"set\(FT_VERSION (.*)\)", content).group(1)
    latest_version = latest_version.strip('"')
    return latest_version


def get_requirements():
    with open("requirements.txt", encoding="utf8") as f:
        requirements = f.read().splitlines()

    torch_requirement = os.environ.get("FT_TORCH_REQUIREMENT", "").strip()
    if torch_requirement:
        requirements = [r for r in requirements if not r.strip().startswith("torch")]
        requirements.append(torch_requirement)

    return requirements


package_name = "fast_rnnt"

setuptools.setup(
    name=package_name,
    version=get_package_version(),
    author="Next-gen Kaldi Team",
    author_email="wkang@pku.edu.cn",
    package_dir={
        package_name: "fast_rnnt/python/fast_rnnt",
    },
    packages=[package_name],
    url="https://github.com/k2-fsa/fast_rnnt",
    description="Fast and memory-efficient RNN-T loss.",
    long_description=read_long_description(),
    long_description_content_type="text/markdown",
    install_requires=get_requirements(),
    ext_modules=[cmake_extension("_fast_rnnt")],
    cmdclass={"build_ext": BuildExtension},
    zip_safe=False,
    classifiers=[
        "Programming Language :: C++",
        "Programming Language :: Python",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    license="Apache licensed, as found in the LICENSE file",
)
