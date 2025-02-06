import json
import requests
from boto3 import client
from botocore.exceptions import ClientError
import os
import time
from typing import Dict, Tuple, Any
import logging

# Constants
GLOBAL_SECURITI_URL = "https://app.securiti.ai"
TIMEOUT = int(os.getenv("TIMEOUT", 30))
RETRIES = int(os.getenv("RETRIES", 3))

# Logger configuration
logger = logging.getLogger()
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Global variables
data_dsr = {}
data_subtask = {}


def log_event(
    level: str,
    event: str,
    status: str,
    message: str = "",
    **kwargs,
):
    """Logs an event with the specified level and details."""
    log_entry = create_log_entry(event, status, message, **kwargs)
    log_message = json.dumps(log_entry)
    if level == "info":
        logger.info(log_message)
    elif level == "warning":
        logger.warning(log_message)
    elif level == "error":
        logger.error(log_message)


def safe_get(data, key, default="unknown"):
    if data is None:
        return default
    return data.get(key, default)


def create_log_entry(
    event: str,
    status: str,
    message: str,
    **kwargs,
) -> Dict[str, Any]:
    """Creates a log entry dictionary."""
    log_entry = {
        "event": event,
        "status": status,
        "lambda_name": safe_get(data_dsr, "lambda_name"),
        "enviroment": safe_get(data_dsr, "enviroment"),
        "form_title": safe_get(data_dsr, "dsp_form_title"),
        "ticket_id": safe_get(data_dsr, "ticketId"),
        "task_id": safe_get(data_subtask, "task_id"),
        "subtask_id": safe_get(data_subtask, "subtask_id"),
        "subtask_title": safe_get(data_subtask, "title"),
        "message": message,
    }
    log_entry.update(kwargs)
    return log_entry


def format_teams_notification(log_entry: Dict[str, Any]) -> Dict[str, Any]:
    """Formats a Microsoft Teams notification."""
    body = [
        {
            "type": "TextBlock",
            "text": f"**Lambda:** {log_entry['lambda_name']}",
            "wrap": True,
            "fontType": "Monospace",
        },
        {
            "type": "TextBlock",
            "text": f"**Ambiente:** {log_entry['enviroment']}",
            "wrap": True,
            "fontType": "Monospace",
        },
        {
            "type": "TextBlock",
            "text": f"**Formulário:** {log_entry['form_title']}",
            "wrap": True,
            "fontType": "Monospace",
        },
        {
            "type": "TextBlock",
            "text": f"**Ticket ID:** {log_entry['ticket_id']}",
            "wrap": True,
            "fontType": "Monospace",
        },
        {
            "type": "TextBlock",
            "text": f"**Tarefa ID:** {log_entry['task_id']}",
            "wrap": True,
            "fontType": "Monospace",
        },
        {
            "type": "TextBlock",
            "text": f"**Subtask ID:** {log_entry['subtask_id']}",
            "wrap": True,
            "fontType": "Monospace",
        },
        {
            "type": "TextBlock",
            "text": f"**Nome da Subtarefa:** {log_entry['subtask_title']}",
            "wrap": True,
            "fontType": "Monospace",
        },
        {
            "type": "TextBlock",
            "text": f"**Mensagem:** {log_entry['message']}",
            "wrap": True,
            "fontType": "Monospace",
        },
    ]

    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.0",
                    "body": body,
                    "actions": [
                        {
                            "type": "Action.OpenUrl",
                            "title": "Visualizar na Securiti",
                            "url": f"https://app.securiti.ai/#/ticket-details/{log_entry['ticket_id']}?tab=WORKLIST",
                        }
                    ],
                },
            }
        ],
    }


def get_secret(secret: str) -> Dict[str, Any]:
    """Fetch secrets from AWS Secrets Manager."""
    log_event("info", "collecting_secrets", "started", message="Collecting secrets")
    sm = client(service_name="secretsmanager", region_name="us-east-1")
    try:
        get_secret_value_response = sm.get_secret_value(SecretId=secret)
        secret_data = json.loads(get_secret_value_response["SecretString"])
        log_event("info", "collecting_secrets", "success", message="Secrets collected")
        return secret_data
    except ClientError as err:
        log_event("error", "collecting_secrets", "error", message=str(err))
        raise RuntimeError("Failed to retrieve secrets") from err


def update_subtask() -> Tuple[bool, str]:
    """Updates the status of a subtask using the API with additional verification."""
    update_url = f"{GLOBAL_SECURITI_URL}/privaci/v1/admin/dsr/subtasks/{data_subtask['subtask_id']}/response/"
    body = {
        "status": 3,
        "entries": [
            {
                "entry_id": 2,
                "entry_type": "question",
                "entry_title": "Success Confirmed",
                "entry_value": "true",
            }
        ],
    }
    error = ""
    for attempt in range(RETRIES):
        try:
            log_event(
                "info",
                "update_subtask",
                "started",
                f"attempt: {attempt+1}",
            )
            response = requests.post(
                url=update_url,
                headers=data_dsr["secrets_header"],
                json=body,
                timeout=TIMEOUT,
            )
            if response.status_code == 200:
                if response.json().get("status") == 0:
                    log_event(
                        "info",
                        "subtask_update",
                        "started",
                        "Updated",
                    )
                    return True, ""
                else:
                    error = f"API returned unexpected status: {response.json().get('status')}"
                    log_event(
                        "error",
                        "subtask_update",
                        "error",
                        error,
                    )
                    return False, error
            else:
                error = f"Error updating subtask. Status code: {response.status_code}. Response: {response.text}"
                log_event(
                    "error",
                    "subtask_update",
                    "http_error",
                    f"status code: {response.status_code} - response: {response.text}",
                )
        except requests.exceptions.Timeout:
            log_event("error", "subtask_update", "timeout", "Timeout")
        except requests.exceptions.RequestException as err:
            log_event("error", "subtask_update", "exception", str(err))
            return False, str(err)

    log_event(
        "error",
        "subtask_update",
        "failure",
        "All retries failed.",
    )
    return False, error


def add_attachment_to_subtask() -> bool:
    file_path = "/tmp/anexoPortabilidade.txt"

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(data_dsr["uid"])

        url = f"{GLOBAL_SECURITI_URL}/privaci/v1/admin/dsr/subtasks/{data_subtask['subtask_id']}/attachment/"

        with open(file_path, "rb") as file:
            files = [("uploadFile", ("anexoPortabilidade.txt", file, "text/plain"))]

            r = requests.post(
                url=url,
                headers=data_dsr["secrets_header"],
                files=files,
                timeout=TIMEOUT,
            )

        os.remove(file_path)
    except Exception as err:
        log_event(
            "error",
            "include_attachment_in_report",
            "error",
            f"Failed to include attachment in report: {err}",
        )
        return False
    return True


def include_attachment_in_report() -> bool:
    url = f"{GLOBAL_SECURITI_URL}/privaci/v1/admin/dsr/subtasks/{data_subtask['subtask_id']}/report_inclusion/"
    url_file_id = f"{GLOBAL_SECURITI_URL}/privaci/v1/admin/dsr/subtasks/{data_subtask['subtask_id']}/response/"

    file_id = None

    try:
        r = requests.get(
            url=url_file_id, headers=data_dsr["secrets_header"], timeout=TIMEOUT
        )
        file_id = r.json()["data"][0]["id"]
    except Exception as err:
        log_event(
            "error",
            "include_attachment_in_report",
            "error",
            f"Failed to get file id: {err}",
        )
        return False

    body = {"file_id": file_id, "include_in_report": True}

    try:
        r = requests.patch(url=url, headers=data_dsr["secrets_header"], json=body)
    except Exception as err:
        log_event(
            "error",
            "include_attachment_in_report",
            "error",
            f"Failed to include attachment in report: {err}",
        )
        return False
    return True


def publish_dsr() -> bool:
    url = f"{GLOBAL_SECURITI_URL}/privaci/v1/admin/dsr/tickets/{data_dsr['ticketId']}/publish"

    try:
        r = requests.post(
            url=url,
            headers=data_dsr["secrets_header"],
            json={
                "publish_subject": "Portabilidade",
                "publish_body": "<p>Solicitação atendida em tela via SAP CDC</p>",
                "override_default_message": False,
                "consolidated_report": False,
                "include_system_names": False,
                "include_pd_names": False,
                "include_pd_value": False,
                "hide_process_info": False,
                "skip_completion_notification": False,
                "merge_pdf_files": False,
                "hide_subtask_details": False,
                "regenerate_report": False,
            },
            timeout=TIMEOUT,
        )
    except Exception as err:
        log_event(
            "error",
            "publish_dsr",
            "error",
            message=f"Failed to publish DSR: {err}",
        )
        return False
    if r.status_code == 200:
        if r.json()["status"] == 0:
            log_event(
                "info",
                "publish_dsr",
                "success",
                message="DSR published successfully.",
            )
            return True
    else:
        log_event(
            "error",
            "publish_dsr",
            "error",
            message=f"Failed to publish DSR. Check: {r.status_code}, Message: {r.text}",
        )
        return False


def process_subtasks():
    """Processes each subtask individually and only sends notifications for definitive failures."""
    for subtask in data_dsr["task_subtask"]:
        global data_subtask
        data_subtask = subtask
        log_event(
            "info",
            "update_subtask",
            "started",
            "Subtask update started",
        )

        attachment = add_attachment_to_subtask()
        if attachment:
            report = include_attachment_in_report()
            if report:
                success, reason = update_subtask()
            if not success:
                log_event("error", "subtask_update", "failed", reason)
                message = create_log_entry(
                    event="subtask_update",
                    status="failed",
                    message=reason,
                )
                send_teams_notification(message)
                send_google_chat_notification(message)
                return

    result = publish_dsr()

    if result:
        log_event(
            "info",
            "publish_dsr",
            "success",
            message="DSR published successfully.",
        )
    else:
        log_event("error", "publish_dsr", "error", message="Failed to publish DSR.")
        message = create_log_entry(
            event="publish_dsr",
            status="error",
            message="Failed to publish DSR.",
        )
        send_teams_notification(message)
        send_google_chat_notification(message)


def format_google_chat_notification(log_entry: Dict[str, Any]) -> Dict[str, Any]:
    """Formats a notification for Google Chat in card format."""
    ticket_id = log_entry["ticket_id"]
    url = f"https://app.securiti.ai/#/ticket-details/{ticket_id}?tab=WORKLIST"

    return {
        "cards": [
            {
                "header": {
                    "title": "Subtask Update",
                    "subtitle": f"Ticket ID: {log_entry['ticket_id']} | Subtask ID: {log_entry['subtask_id']}",
                },
                "sections": [
                    {
                        "widgets": [
                            {
                                "textParagraph": {
                                    "text": (
                                        f"<b>Lambda:</b> {log_entry['lambda_name']}<br>"
                                        f"<b>Ambiente:</b> {log_entry['enviroment']}<br>"
                                        f"<b>Formulário:</b> {log_entry['form_title']}<br>"
                                        f"<b>Tarefa ID:</b> {log_entry['task_id']}<br>"
                                        f"<b>Subtarefa ID:</b> {log_entry['subtask_id']}<br>"
                                        f"<b>Nome da Subtarefa:</b> {log_entry['subtask_title']}<br>"
                                        f"<b>Mensagem:</b> {log_entry['message']}<br>"
                                        f"<b>Link para o Ticket:</b> <a href='{url}'>Visualizar na Securiti</a>"
                                    )
                                }
                            }
                        ]
                    }
                ],
            }
        ]
    }


def send_google_chat_notification(log_entry: Dict[str, Any]):
    """Sends a notification to Google Chat."""
    payload = format_google_chat_notification(log_entry)

    response = requests.post(
        data_dsr["googleChat"],
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
    )

    if response.status_code != 200:
        log_event(
            "error",
            "send_google_chat_notification",
            "error",
            "Failed to send notification to Google Chat.",
        )
    else:
        log_event(
            "info",
            "send_google_chat_notification",
            "success",
            "Notification successfully sent to Google Chat.",
        )


def send_teams_notification(log_entry: Dict[str, Any]):
    """Sends a notification to Microsoft Teams."""
    payload = format_teams_notification(log_entry)

    response = requests.post(
        data_dsr["microsoftTeams"],
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
    )

    if response.status_code != 202:
        log_event(
            "error",
            "send_teams_notification",
            "error",
            "Failed to send notification to Teams.",
        )
    else:
        log_event(
            "info",
            "send_teams_notification",
            "success",
            "Notification successfully sent to Teams.",
        )


def main(event, context):
    """Main function that processes a list of tasks and updates their subtasks."""
    global data_dsr
    log_event(
        "info",
        "main",
        "started",
        message="Main function started",
        context=str(context),
        event_data=event,
    )

    try:
        data_dsr = json.loads(event["data"].replace("'", '"'))
    except (KeyError, json.JSONDecodeError) as e:
        log_event("error", "main", "error", message=str(e))
        return {
            "statusCode": 400,
            "body": json.dumps({"message": "Invalid input data", "error": str(e)}),
        }

    try:
        data_dsr["lambda_name"] = os.getenv(
            "AWS_LAMBDA_FUNCTION_NAME", "default_lambda"
        )

        if "uat" in (data_dsr["sm"].replace("{type}", "dsr")):
            data_dsr["enviroment"] = "UAT"
        else:
            data_dsr["enviroment"] = "PROD"

        secret_path_token = (data_dsr["sm"].replace("{type}", "dsr")) + "token"
        secret_path_channel = (data_dsr["sm"].replace("{type}", "global")) + "channel"

        secret_data_channel = get_secret(secret_path_channel)
        secret_data_token = get_secret(secret_path_token)

        data_dsr["googleChat"] = secret_data_channel.get("googleChat")
        data_dsr["microsoftTeams"] = secret_data_channel.get("microsoftTeams")
        data_dsr["secrets_header"] = {
            "X-API-KEY": secret_data_token.get("X-API-KEY"),
            "X-API-SECRET": secret_data_token.get("X-API-SECRET"),
            "X-TIDENT": secret_data_token.get("X-TIDENT"),
        }

    except RuntimeError as e:
        return {"statusCode": 401, "body": json.dumps({"message": str(e)})}

    process_subtasks()

    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "All subtasks processed with notifications sent for failures.",
                "dsr_id": data_dsr["ticketId"],
            }
        ),
    }
