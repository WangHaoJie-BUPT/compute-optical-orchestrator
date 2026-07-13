"""Load Kubernetes credentials locally or from a Pod ServiceAccount."""

from __future__ import annotations

import os

from kubernetes import config
from kubernetes.config.config_exception import ConfigException


class KubernetesClientConfigurationError(RuntimeError):
    """Raised when Kubernetes client credentials cannot be loaded."""


def load_kubernetes_client_configuration(
    context: str | None = None,
) -> str:
    """
    Load Kubernetes client credentials.

    Modes:
    - auto: use in-cluster credentials inside a Pod, otherwise kubeconfig;
    - incluster: require ServiceAccount credentials;
    - kubeconfig: require a local kubeconfig.
    """

    mode = os.environ.get(
        "KUBERNETES_CLIENT_MODE",
        "auto",
    ).strip().lower()

    if mode not in {
        "auto",
        "incluster",
        "in-cluster",
        "kubeconfig",
    }:
        raise KubernetesClientConfigurationError(
            "KUBERNETES_CLIENT_MODE must be "
            "'auto', 'incluster' or 'kubeconfig'."
        )

    running_in_cluster = bool(
        os.environ.get("KUBERNETES_SERVICE_HOST")
        and os.environ.get("KUBERNETES_SERVICE_PORT")
    )

    if mode in {"incluster", "in-cluster"}:
        try:
            config.load_incluster_config()
        except ConfigException as exc:
            raise KubernetesClientConfigurationError(
                "In-cluster Kubernetes credentials "
                "could not be loaded."
            ) from exc

        return "in-cluster"

    if mode == "kubeconfig":
        try:
            config.load_kube_config(
                context=context,
            )
        except ConfigException as exc:
            raise KubernetesClientConfigurationError(
                "Local kubeconfig could not be loaded."
            ) from exc

        return context or "kubeconfig-current-context"

    if running_in_cluster:
        try:
            config.load_incluster_config()
        except ConfigException as exc:
            raise KubernetesClientConfigurationError(
                "The process appears to run inside Kubernetes, "
                "but ServiceAccount credentials are unavailable."
            ) from exc

        return "in-cluster"

    try:
        config.load_kube_config(
            context=context,
        )
    except ConfigException as exc:
        raise KubernetesClientConfigurationError(
            "Local kubeconfig could not be loaded."
        ) from exc

    return context or "kubeconfig-current-context"
