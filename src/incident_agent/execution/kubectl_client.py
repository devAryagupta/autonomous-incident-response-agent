"""Structured Kubernetes mutation client (no shell string injection).

Mirrors the observation-provider pattern: Fake for tests, Live via kubernetes SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class MutationRecord:
    """Audit entry recorded by FakeKubectlClient."""

    method: str
    namespace: str
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False


class BaseKubectlClient(ABC):
    """Hexagonal port for allowlisted cluster mutations."""

    @abstractmethod
    def delete_pod(self, namespace: str, name: str, *, dry_run: bool = False) -> dict[str, Any]:
        ...

    @abstractmethod
    def rollout_restart_deployment(
        self,
        namespace: str,
        name: str,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    def rollout_undo_deployment(
        self,
        namespace: str,
        name: str,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    def scale_deployment(
        self,
        namespace: str,
        name: str,
        replicas: int,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_deployment_replicas(self, namespace: str, name: str) -> int | None:
        ...

    @abstractmethod
    def update_container_memory_limit(
        self,
        namespace: str,
        name: str,
        *,
        container_name: str,
        memory_limit: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    def resource_exists(self, kind: str, namespace: str, name: str) -> bool:
        ...


class FakeKubectlClient(BaseKubectlClient):
    """In-memory cluster stand-in — records mutations; dry_run never mutates state."""

    def __init__(
        self,
        *,
        pods: set[tuple[str, str]] | None = None,
        deployments: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> None:
        self.pods = (
            set(pods) if pods is not None else {("default", "payment-service-7d9f8")}
        )
        self.deployments = (
            dict(deployments)
            if deployments is not None
            else {
                ("default", "payment-service"): {
                    "replicas": 2,
                    "containers": {"app": {"memory_limit": "256Mi"}},
                }
            }
        )
        self.mutations: list[MutationRecord] = []
        self.lookups: list[tuple[str, str, str]] = []

    def delete_pod(self, namespace: str, name: str, *, dry_run: bool = False) -> dict[str, Any]:
        self.mutations.append(
            MutationRecord("delete_pod", namespace, name, dry_run=dry_run)
        )
        if dry_run:
            return {"dry_run": True, "deleted": name}
        self.pods.discard((namespace, name))
        return {"deleted": name}

    def rollout_restart_deployment(
        self,
        namespace: str,
        name: str,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        stamp = datetime.now(tz=UTC).isoformat()
        self.mutations.append(
            MutationRecord(
                "rollout_restart_deployment",
                namespace,
                name,
                params={"restartedAt": stamp},
                dry_run=dry_run,
            )
        )
        if dry_run:
            return {"dry_run": True, "restartedAt": stamp}
        dep = self.deployments.setdefault(
            (namespace, name),
            {"replicas": 1, "containers": {"app": {"memory_limit": "256Mi"}}},
        )
        dep["restartedAt"] = stamp
        return {"restartedAt": stamp}

    def rollout_undo_deployment(
        self,
        namespace: str,
        name: str,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        self.mutations.append(
            MutationRecord("rollout_undo_deployment", namespace, name, dry_run=dry_run)
        )
        if dry_run:
            return {"dry_run": True, "undone": name}
        dep = self.deployments.get((namespace, name))
        if dep is not None:
            dep.pop("restartedAt", None)
        return {"undone": name}

    def scale_deployment(
        self,
        namespace: str,
        name: str,
        replicas: int,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        self.mutations.append(
            MutationRecord(
                "scale_deployment",
                namespace,
                name,
                params={"replicas": replicas},
                dry_run=dry_run,
            )
        )
        if dry_run:
            return {"dry_run": True, "replicas": replicas}
        dep = self.deployments.setdefault(
            (namespace, name),
            {"replicas": replicas, "containers": {"app": {"memory_limit": "256Mi"}}},
        )
        previous = int(dep.get("replicas", 0))
        dep["replicas"] = replicas
        dep["previous_replicas"] = previous
        return {"replicas": replicas, "previous_replicas": previous}

    def get_deployment_replicas(self, namespace: str, name: str) -> int | None:
        dep = self.deployments.get((namespace, name))
        if dep is None:
            return None
        return int(dep.get("replicas", 0))

    def update_container_memory_limit(
        self,
        namespace: str,
        name: str,
        *,
        container_name: str,
        memory_limit: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        self.mutations.append(
            MutationRecord(
                "update_container_memory_limit",
                namespace,
                name,
                params={
                    "container_name": container_name,
                    "memory_limit": memory_limit,
                },
                dry_run=dry_run,
            )
        )
        if dry_run:
            return {"dry_run": True, "memory_limit": memory_limit}
        dep = self.deployments.setdefault(
            (namespace, name),
            {"replicas": 1, "containers": {}},
        )
        containers = dep.setdefault("containers", {})
        previous = (containers.get(container_name) or {}).get("memory_limit")
        containers[container_name] = {"memory_limit": memory_limit}
        return {
            "memory_limit": memory_limit,
            "previous_memory_limit": previous,
            "container_name": container_name,
        }

    def resource_exists(self, kind: str, namespace: str, name: str) -> bool:
        kind_l = kind.lower()
        self.lookups.append((kind_l, namespace, name))
        if kind_l == "pod":
            return (namespace, name) in self.pods
        if kind_l == "deployment":
            return (namespace, name) in self.deployments
        return False


class LiveKubectlClient(BaseKubectlClient):
    """
    Live mutations via the official Kubernetes Python client (typed API objects).

    Never shells out to kubectl. Requires optional dependency: pip install -e ".[k8s]"
    """

    def __init__(self, *, context: str | None = None) -> None:
        try:
            from kubernetes import client, config  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "Install the optional 'k8s' extra: pip install -e \".[k8s]\""
            ) from exc

        if context:
            config.load_kube_config(context=context)
        else:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()

        self._core = client.CoreV1Api()
        self._apps = client.AppsV1Api()
        self._client_mod = client

    def delete_pod(self, namespace: str, name: str, *, dry_run: bool = False) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if dry_run:
            kwargs["dry_run"] = "All"
        self._core.delete_namespaced_pod(name=name, namespace=namespace, **kwargs)
        return {"deleted": name, "dry_run": dry_run}

    def rollout_restart_deployment(
        self,
        namespace: str,
        name: str,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        stamp = datetime.now(tz=UTC).isoformat()
        body = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": stamp,
                        }
                    }
                }
            }
        }
        kwargs: dict[str, Any] = {"body": body}
        if dry_run:
            kwargs["dry_run"] = "All"
        self._apps.patch_namespaced_deployment(
            name=name,
            namespace=namespace,
            **kwargs,
        )
        return {"restartedAt": stamp, "dry_run": dry_run}

    def rollout_undo_deployment(
        self,
        namespace: str,
        name: str,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        # Roll back to previous ReplicaSet by patching deployment template rollback annotation
        # via AppsV1 rollback if available; otherwise scale-annotation clear is insufficient.
        # Use create_namespaced_deployment_rollback when present.
        rollback = getattr(self._apps, "create_namespaced_deployment_rollback", None)
        if rollback is None:
            # Fallback: remove restartedAt annotation (best-effort undo of restart patch).
            body = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": None,
                            }
                        }
                    }
                }
            }
            kwargs: dict[str, Any] = {"body": body}
            if dry_run:
                kwargs["dry_run"] = "All"
            self._apps.patch_namespaced_deployment(name=name, namespace=namespace, **kwargs)
            return {"undone": name, "dry_run": dry_run, "mode": "annotation_clear"}

        body = self._client_mod.V1DeploymentRollback(
            name=name,
            rollback_to=self._client_mod.V1RollbackConfig(revision=0),
        )
        kwargs = {"body": body}
        if dry_run:
            kwargs["dry_run"] = "All"
        rollback(name=name, namespace=namespace, **kwargs)
        return {"undone": name, "dry_run": dry_run, "mode": "rollback_api"}

    def scale_deployment(
        self,
        namespace: str,
        name: str,
        replicas: int,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        body = {"spec": {"replicas": replicas}}
        kwargs: dict[str, Any] = {"body": body}
        if dry_run:
            kwargs["dry_run"] = "All"
        previous = self.get_deployment_replicas(namespace, name)
        self._apps.patch_namespaced_deployment_scale(
            name=name,
            namespace=namespace,
            **kwargs,
        )
        return {
            "replicas": replicas,
            "previous_replicas": previous,
            "dry_run": dry_run,
        }

    def get_deployment_replicas(self, namespace: str, name: str) -> int | None:
        try:
            scale = self._apps.read_namespaced_deployment_scale(
                name=name, namespace=namespace
            )
        except Exception:  # noqa: BLE001
            return None
        spec = getattr(scale, "spec", None)
        replicas = getattr(spec, "replicas", None) if spec else None
        return int(replicas) if replicas is not None else None

    def update_container_memory_limit(
        self,
        namespace: str,
        name: str,
        *,
        container_name: str,
        memory_limit: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        dep = self._apps.read_namespaced_deployment(name=name, namespace=namespace)
        containers = getattr(getattr(dep.spec, "template").spec, "containers", []) or []
        previous = None
        found = False
        for container in containers:
            if getattr(container, "name", None) != container_name:
                continue
            found = True
            resources = getattr(container, "resources", None) or self._client_mod.V1ResourceRequirements()
            limits = dict(getattr(resources, "limits", None) or {})
            previous = limits.get("memory")
            limits["memory"] = memory_limit
            resources.limits = limits
            container.resources = resources
            break
        if not found:
            raise ValueError(f"container {container_name!r} not found on deployment/{name}")

        kwargs: dict[str, Any] = {"body": dep}
        if dry_run:
            kwargs["dry_run"] = "All"
        self._apps.replace_namespaced_deployment(
            name=name,
            namespace=namespace,
            **kwargs,
        )
        return {
            "container_name": container_name,
            "memory_limit": memory_limit,
            "previous_memory_limit": previous,
            "dry_run": dry_run,
        }

    def resource_exists(self, kind: str, namespace: str, name: str) -> bool:
        kind_l = kind.lower()
        try:
            if kind_l == "pod":
                self._core.read_namespaced_pod(name=name, namespace=namespace)
                return True
            if kind_l == "deployment":
                self._apps.read_namespaced_deployment(name=name, namespace=namespace)
                return True
        except Exception:  # noqa: BLE001
            return False
        return False


def live_kubectl_client(*, context: str | None = None) -> LiveKubectlClient:
    return LiveKubectlClient(context=context)
