from kubernetes import client, config
import os
import json
import asyncio
import anyio
import logging
from rabbitmq import RabbitMQ

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
BASE_PATH = os.getenv("BASE_PATH", "/app/databases/")  # ahora apunta al PVC montado
NAMESPACE_AGENTS = os.getenv("AGENTS_NAMESPACE", "agents")

rabbitmq = RabbitMQ()

# Cargar config dentro del cluster
config.load_incluster_config()
apps_api = client.AppsV1Api()
core_api = client.CoreV1Api()


async def create_agent_resources(agent_id: str, prompt: str):
    deployment_name = f"agent-{agent_id}"
    service_name = f"agent-{agent_id}"
    labels = {"app": deployment_name}

    # 1) Deployment del agente
    container = client.V1Container(
        name="agent",
        image="us-east1-docker.pkg.dev/deplo-478916/deplo/deploy-template:v1",
        env=[
            client.V1EnvVar(name="AGENT_ID", value=agent_id),
            client.V1EnvVar(name="GOOGLE_API_KEY", value=GOOGLE_API_KEY),
            client.V1EnvVar(name="PROMPT", value=prompt),
            client.V1EnvVar(name="BASE_PATH", value=BASE_PATH),
        ],

        ports=[client.V1ContainerPort(container_port=8000)],
        volume_mounts=[
            client.V1VolumeMount(
                name="db-volume",
                mount_path="/app/database",
                sub_path=agent_id  # cada agente en su subcarpeta dentro del PVC
            )
        ],
    )

    pod_spec = client.V1PodSpec(
        containers=[container],
        volumes=[
            client.V1Volume(
                name="db-volume",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                    claim_name="nfs-databases-pvc"
                )
            )
        ]
    )

    pod_template = client.V1PodTemplateSpec(
        metadata=client.V1ObjectMeta(labels=labels),
        spec=pod_spec
    )

    deployment_spec = client.V1DeploymentSpec(
        replicas=1,
        selector=client.V1LabelSelector(match_labels=labels),
        template=pod_template
    )

    deployment = client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=client.V1ObjectMeta(name=deployment_name),
        spec=deployment_spec
    )

    # 2) Service del agente
    service = client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=client.V1ObjectMeta(name=service_name),
        spec=client.V1ServiceSpec(
            selector=labels,
            ports=[client.V1ServicePort(port=8000, target_port=8000)]
        )
    )

    # Crear o reemplazar deployment y service
    try:
        apps_api.create_namespaced_deployment(namespace=NAMESPACE_AGENTS, body=deployment)
        logging.info(f"Deployment {deployment_name} created")
    except client.exceptions.ApiException as e:
        if e.status == 409:
            apps_api.replace_namespaced_deployment(name=deployment_name, namespace=NAMESPACE_AGENTS, body=deployment)
            logging.info(f"Deployment {deployment_name} replaced")
        else:
            raise

    try:
        core_api.create_namespaced_service(namespace=NAMESPACE_AGENTS, body=service)
        logging.info(f"Service {service_name} created")
    except client.exceptions.ApiException as e:
        if e.status == 409:
            core_api.replace_namespaced_service(name=service_name, namespace=NAMESPACE_AGENTS, body=service)
            logging.info(f"Service {service_name} replaced")
        else:
            raise


async def callback(message):
    try:
        decoded_message = message.body.decode().strip()
        logging.info(f"Message received with content = {decoded_message}")

        payload = json.loads(decoded_message)
        agent_id = payload.get("agent_id")

        # Ruta al prompt en el deploy worker (montado desde /nfs/prompts)
        prompt_path = f"/app/prompts/{agent_id}/prompt.txt"
        prompt = ""
        async with await anyio.open_file(prompt_path, "r") as f:
            prompt += await f.read()

        await create_agent_resources(agent_id, prompt)

        logging.info(f"Agent {agent_id} deployed as Service agent-{agent_id} in namespace {NAMESPACE_AGENTS}")

    except Exception as e:
        logging.error(f"Error processing message: {e}")


async def main():
    await rabbitmq.consume("deploy", callback)


if __name__ == "__main__":
    asyncio.run(main())
