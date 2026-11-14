#!/usr/bin/env bats

@test "bootstrap help works without root or downloads" {
    run bash install.sh --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"G0d0v Remnawave Installer"* ]]
}

@test "bootstrap version is consistent with runtime" {
    run bash install.sh --version
    [ "$status" -eq 0 ]
    [ "$output" = "$(cat VERSION)" ]
}

@test "compatibility check does not install the CLI" {
    run bash install.sh --check
    [ "$status" -eq 0 ]
    [[ "$output" == *"No changes made"* ]]
    [ ! -e /opt/godov-remnawave-cli ]
}

@test "non-root install fails before accessing the network" {
    run bash install.sh install --config /does/not/exist --yes
    [ "$status" -ne 0 ]
    [[ "$output" == *"root"* ]]
}
