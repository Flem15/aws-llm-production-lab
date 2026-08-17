#!/usr/bin/env python3

HOURS_PER_MONTH = 730

NAT_GATEWAY_HOURLY = 0.045
NAT_GATEWAY_PER_GB = 0.045

PUBLIC_IPV4_HOURLY = 0.005

INTERFACE_ENDPOINT_HOURLY_PER_AZ = 0.01
INTERFACE_ENDPOINT_PER_GB = 0.01

INTERFACE_ENDPOINT_SERVICES = 3
AVAILABILITY_ZONES = 2


def monthly_fixed_costs() -> dict[str, float]:
    nat_gateway = (
        NAT_GATEWAY_HOURLY
        * HOURS_PER_MONTH
    )

    public_ipv4 = (
        PUBLIC_IPV4_HOURLY
        * HOURS_PER_MONTH
    )

    endpoint_enis = (
        INTERFACE_ENDPOINT_SERVICES
        * AVAILABILITY_ZONES
    )

    private_endpoints = (
        endpoint_enis
        * INTERFACE_ENDPOINT_HOURLY_PER_AZ
        * HOURS_PER_MONTH
    )

    return {
        "natGateway":
            nat_gateway,
        "natPublicIpv4":
            public_ipv4,
        "natFixedTotal":
            nat_gateway
            + public_ipv4,
        "privateEndpointFixedTotal":
            private_endpoints,
        "privateEndpointEnis":
            float(
                endpoint_enis
            ),
    }


def main() -> None:
    costs = (
        monthly_fixed_costs()
    )

    nat = costs[
        "natFixedTotal"
    ]

    endpoints = costs[
        "privateEndpointFixedTotal"
    ]

    difference = (
        endpoints - nat
    )

    print(
        "Day 13 fixed-cost comparison"
    )

    print(
        "----------------------------"
    )

    print(
        f"NAT Gateway:        "
        f"${costs['natGateway']:.2f}/month"
    )

    print(
        f"NAT public IPv4:    "
        f"${costs['natPublicIpv4']:.2f}/month"
    )

    print(
        f"NAT fixed total:    "
        f"${nat:.2f}/month"
    )

    print()

    print(
        f"Interface ENIs:     "
        f"{int(costs['privateEndpointEnis'])}"
    )

    print(
        f"Endpoints fixed:    "
        f"${endpoints:.2f}/month"
    )

    print(
        "S3 gateway:         "
        "$0.00/month endpoint fee"
    )

    print()

    if difference > 0:
        print(
            "RESULT: PrivateLink fixed "
            f"cost is approximately "
            f"${difference:.2f}/month "
            "higher than the current "
            "single-NAT fixed design."
        )

    elif difference < 0:
        print(
            "RESULT: PrivateLink fixed "
            f"cost is approximately "
            f"${abs(difference):.2f}/month "
            "lower than the current "
            "single-NAT fixed design."
        )

    else:
        print(
            "RESULT: Fixed costs are equal."
        )

    print()

    print(
        "Data-processing charges are "
        "usage-dependent and are not "
        "included in this fixed-cost comparison."
    )


if __name__ == "__main__":
    main()
