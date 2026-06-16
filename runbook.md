# Operations Runbook — PR Review Agent

This document provides operational instructions, troubleshooting procedures, and configuration guidance for running the **PR Review Agent** in production.

---

## 1. Architecture Overview

The PR Review Agent runs as a serverless containerized application on AWS Lambda:

*   **API Gateway (HTTP API v2)**: Entrypoint for all incoming GitHub webhooks on `/webhook`.
*   **WebhookFunction (Lambda)**: Handles webhook HMAC signature verification and quickly responds to GitHub (under 10s SLA). Normal PRs (<= 15 files) are reviewed inline in a background task. Large PRs (> 15 files) are queued to SQS.
*   **PRReviewQueue (SQS)**: Asynchronous buffer for large PRs. Messages are automatically retried up to 3 times before routing to the DLQ.
*   **SQSConsumerFunction (Lambda)**: Triggered by SQS to process large PRs asynchronously (supports up to 15-minute executions).
*   **AWS Secrets Manager**: Secure storage for the GitHub PAT, Webhook secret, and OpenAI API Key.

---

## 2. Monitoring & Observability

### Log Groups
All execution logs are written to Amazon CloudWatch Logs:
*   **Webhook Handler Logs**: `/aws/lambda/pr-review-agent-webhook`
*   **SQS Queue Consumer Logs**: `/aws/lambda/pr-review-agent-sqs-consumer`

To search for a specific PR's logs, search the appropriate log group for the PR number (e.g., `PR #42`).

### Configured CloudWatch Alarms
Alarms are set up in the CloudFormation stack to trigger when issues arise:
1.  **WebhookErrorAlarm**: Triggers if the `WebhookFunction` error rate exceeds 5% over 5 minutes.
2.  **SQSConsumerErrorAlarm**: Triggers if the `SQSConsumerFunction` error rate exceeds 5% over 5 minutes.
3.  **SQSConsumerP95DurationAlarm**: Triggers if 95% of large PR reviews take longer than 120 seconds.
4.  **DLQDepthAlarm**: Triggers if any review fails processing 3 times and is moved to the Dead Letter Queue (`pr-review-dlq`).
5.  **GitHubAuthErrorAlarm**: Triggers if the agent logs a `GitHub API 401: Bad credentials` error, indicating a token issue.

---

## 3. Configuration & Secrets Rotation

### Editing Settings
All settings (file thresholds, limits, skipped file types) can be updated by setting them as keys in the AWS Secrets Manager JSON secret `pr-review-agent/secrets` or by adding them to the environment variables of the Lambda functions.

*   `MAX_FILES_PER_PR` (default: 10)
*   `MAX_DIFF_CHARS` (default: 8000)
*   `LARGE_PR_THRESHOLD` (default: 15)
*   `SKIP_EXTENSIONS` (default: `.lock,.svg,.png,.jpg,.jpeg,.gif,.json,.csv,.md,.txt,.yaml,.yml`)

### Secrets Rotation (GitHub Token / OpenAI Key)
If you need to rotate a secret:
1.  Go to the **AWS Secrets Manager** console.
2.  Retrieve the `pr-review-agent/secrets` secret.
3.  Click **Edit** and update the value (e.g., `OPENAI_API_KEY` or `GITHUB_TOKEN`).
4.  Save the changes.

*Note: Since the FastAPI application loads secrets on container cold start (`main.py` lifespan hook), rotation will take effect automatically as new Lambda containers spin up. To force immediate rotation, you can run a simple update on the Lambda configurations (e.g., redeploying or updating an environment variable description) to force execution environments to recycle.*

---

## 4. Required Status Checks Setup (GitHub)

To make the PR Review Agent status check a hard requirement before PRs can be merged:
1.  Go to your repository settings -> **Branches**.
2.  Add or edit the branch protection rule for your target branches (e.g., `main` or `dev`).
3.  Enable **Require status checks to pass before merging**.
4.  In the search field, search for **`PR Review Agent`**.
    *(Note: The status check context will only be visible in this search box after the agent has successfully posted a status check on the repository at least once).*
5.  Check the box for `PR Review Agent` and save.

---

## 5. Fail-Open Behavior & Troubleshooting

### Fail-Open Policy
To prevent developers from being blocked if the agent is down, the agent employs a strict **Fail-Open Policy**:
*   If the agent encounters an exception, it posts a GitHub commit status state of **`success`** with the description `Fail-open: <error>`.
*   This satisfies the required status check constraint in branch protection, allowing the developer to merge.
*   The agent also posts a non-blocking issue comment notifying the developer that the review crashed and failed open.

### Empty DLQ Handling
If the `DLQDepthAlarm` fires:
1.  Locate the failed messages in the `pr-review-dlq` SQS queue.
2.  Review the `SQSConsumerFunction` logs in CloudWatch around the time of the failure to identify the exception.
3.  Once the bug is fixed, you can redrive the messages back to the primary `pr-review-queue` for reprocessing using the **Start DLQ Redrive** feature in the AWS SQS console.
