# tortuga-kit-vmwareadapter

**Warning: this kit is currently not functional. It will be updated in coming weeks**

## Overview

This repository contains the requisite files to build a resource adapter kit
to enable support for VMware vSphere 5.x compute nodes in [Tortuga](https://github.com/UnivaCorporation/tortuga).

## Building the kit

Change to subdirectory containing cloned Git repository and run `build-kit`.
`build-kit` is provided by the `tortuga-core` package in the [Tortuga](https://github.com/UnivaCorporation/tortuga) source.
Be sure you have activated the tortuga virtual environment as suggested in the [Tortuga build instructions](https://github.com/UnivaCorporation/tortuga#build-instructions) before executing `build-kit`.

## Installation

Install the kit:

```shell
install-kit kit-vmwareadapter*.tar.bz2
```

See the [Tortuga Installation and Administration Guide](https://github.com/UnivaCorporation/tortuga/blob/v6.3.1-20180512-1/doc/tortuga-6-admin-guide.md) for configuration
details.

[Tortuga]: https://github.com/UnivaCorporation/tortuga "Tortuga"
