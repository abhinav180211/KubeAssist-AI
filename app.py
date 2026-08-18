from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from kubeassist_client import ask_kubeassist
import subprocess
import json

app = Flask(__name__)
CORS(app)


# ================= KUBECTL HELPERS =================
def run_kubectl(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        try:
            return json.loads(result.stdout)
        except:
            return result.stdout
    return None


def get_pods():
    return run_kubectl(["kubectl", "get", "pods", "-A", "-o", "json"])


def get_namespaces():
    data = run_kubectl(["kubectl", "get", "ns", "-o", "json"])
    if not data:
        return []
    return [item["metadata"]["name"] for item in data["items"]]


def get_deployments(namespace):
    data = run_kubectl(["kubectl", "get", "deploy", "-n", namespace, "-o", "json"])
    if not data:
        return []
    return [{
        "name": item["metadata"]["name"],
        "images": [c["image"] for c in item["spec"]["template"]["spec"]["containers"]],
        "selector": item["spec"]["selector"]["matchLabels"]
    } for item in data["items"]]


def extract_pod_info(data):
    services = {}
    for pod in data["items"]:
        pod_name    = pod["metadata"]["name"]
        namespace   = pod["metadata"]["namespace"]
        status      = pod["status"].get("phase", "Unknown")
        pod_ip      = pod["status"].get("podIP", "N/A")
        node        = pod["spec"].get("nodeName", "N/A")
        labels      = pod["metadata"].get("labels", {})

        for container in pod["spec"]["containers"]:
            env_vars  = {}
            for env in container.get("env", []):
                key = env.get("name")
                if "value" in env:
                    env_vars[key] = env["value"]
                elif "valueFrom" in env:
                    ref = env["valueFrom"]
                    if "configMapKeyRef" in ref:
                        cm = ref["configMapKeyRef"]
                        env_vars[key] = f"ConfigMap({cm['name']}:{cm['key']})"
                    elif "secretKeyRef" in ref:
                        sec = ref["secretKeyRef"]
                        env_vars[key] = f"Secret({sec['name']}:{sec['key']})"
                    else:
                        env_vars[key] = "valueFrom"
                else:
                    env_vars[key] = "N/A"

            resources = container.get("resources", {})
            limits    = resources.get("limits", {})
            requests  = resources.get("requests", {})

            services[pod_name] = {
                "pod":        pod_name,
                "namespace":  namespace,
                "status":     status,
                "labels":     labels,
                "node":       node,
                "pod_ip":     pod_ip,
                "image":      container.get("image"),
                "cpu_req":    requests.get("cpu",    "Not Set"),
                "mem_req":    requests.get("memory", "Not Set"),
                "cpu_limit":  limits.get("cpu",      "Not Set"),
                "mem_limit":  limits.get("memory",   "Not Set"),
                "env":        env_vars
            }
    return services


def match_labels(pod_labels, selector):
    if not pod_labels:
        return False
    return all(pod_labels.get(k) == v for k, v in selector.items())


def convert_cpu(cpu):
    if not cpu or cpu == "Not Set":
        return 0
    return int(cpu.replace("m", "")) if "m" in cpu else int(cpu) * 1000


def convert_mem(mem):
    if not mem or mem == "Not Set":
        return 0
    if "Gi" in mem:
        return int(mem.replace("Gi", "")) * 1024
    if "Mi" in mem:
        return int(mem.replace("Mi", ""))
    return 0


# ================= ROUTES =================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/pods")
def api_pods():
    data = get_pods()
    if not data:
        return jsonify({"error": "kubectl failed or no cluster connected"}), 500
    services = extract_pod_info(data)
    return jsonify(list(services.values()))


@app.route("/api/deployments")
def api_deployments():
    pod_data = get_pods()
    if not pod_data:
        return jsonify([])
    services   = extract_pod_info(pod_data)
    namespaces = get_namespaces()
    result     = []

    for ns in namespaces:
        deployments = get_deployments(ns)
        for dep in deployments:
            pods = [
                p for p in services.values()
                if p["namespace"] == ns and match_labels(p["labels"], dep["selector"])
            ]
            unique  = list({p["pod"]: p for p in pods}.values())
            total   = len(unique)
            running = sum(1 for p in unique if p["status"] == "Running")
            result.append({
                "namespace": ns,
                "name":      dep["name"],
                "images":    dep["images"],
                "running":   running,
                "total":     total,
                "healthy":   running == total and total > 0
            })
    return jsonify(result)


@app.route("/api/resources")
def api_resources():
    data = get_pods()
    if not data:
        return jsonify({"error": "kubectl failed"}), 500
    services = extract_pod_info(data)

    cpu_r = cpu_l = mem_r = mem_l = 0
    for s in services.values():
        cpu_r += convert_cpu(s["cpu_req"])
        cpu_l += convert_cpu(s["cpu_limit"])
        mem_r += convert_mem(s["mem_req"])
        mem_l += convert_mem(s["mem_limit"])

    return jsonify({
        "cpu_req_cores": round(cpu_r / 1000, 2),
        "cpu_req_m":     cpu_r,
        "cpu_lim_cores": round(cpu_l / 1000, 2),
        "cpu_lim_m":     cpu_l,
        "mem_req_gi":    round(mem_r / 1024, 2),
        "mem_req_mi":    mem_r,
        "mem_lim_gi":    round(mem_l / 1024, 2),
        "mem_lim_mi":    mem_l,
    })


@app.route("/api/namespaces")
def api_namespaces():
    return jsonify(get_namespaces())


@app.route("/api/logs")
def api_logs():
    pod       = request.args.get("pod")
    namespace = request.args.get("namespace", "default")
    tail      = request.args.get("tail", "100")

    if not pod:
        return jsonify({"error": "pod parameter required"}), 400

    result = subprocess.run(
        ["kubectl", "logs", pod, "-n", namespace, f"--tail={tail}"],
        capture_output=True, text=True
    )
    return jsonify({
        "logs": result.stdout if result.stdout else result.stderr,
        "pod": pod,
        "namespace": namespace
    })


@app.route("/api/describe")
def api_describe():
    pod       = request.args.get("pod")
    namespace = request.args.get("namespace", "default")

    if not pod:
        return jsonify({"error": "pod parameter required"}), 400

    result = subprocess.run(
        ["kubectl", "describe", "pod", pod, "-n", namespace],
        capture_output=True, text=True
    )
    return jsonify({
        "output": result.stdout if result.stdout else result.stderr
    })


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data  = request.json
    query = data.get("query", "").strip()

    if not query:
        return jsonify({"error": "query is required"}), 400

    # Optional: attach live cluster context
    context = data.get("context", None)

    try:
        response = ask_kubeassist(query, context_data=context)
        return jsonify({"response": response})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


# ================= MAIN =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)