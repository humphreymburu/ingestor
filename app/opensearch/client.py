"""Port of ingestor/src/opensearch/client.ts."""

from __future__ import annotations

import boto3
from opensearchpy import OpenSearch, Urllib3AWSV4SignerAuth

from app.config import config


def create_opensearch_client() -> OpenSearch:
    if config.opensearch.auth == "aws":
        # boto3's default credential chain (env vars -> shared config/SSO ->
        # web identity -> EC2/ECS/Fargate task role), same precedence as the
        # AWS SDK v3 `defaultProvider()` the TS client used. Urllib3AWSV4SignerAuth
        # re-fetches/refreshes credentials per request via
        # `credentials.get_frozen_credentials()`, mirroring the TS client's
        # `getCredentials: () => credentialsProvider()`. Works with the
        # default urllib3-based connection class (no `requests` dependency).
        credentials = boto3.Session().get_credentials()
        auth = Urllib3AWSV4SignerAuth(
            credentials, config.opensearch.region, config.opensearch.service
        )
        return OpenSearch(
            hosts=[config.opensearch.node],
            http_auth=auth,
            use_ssl=config.opensearch.node.startswith("https"),
        )

    if config.opensearch.auth == "basic":
        if not config.opensearch.username or not config.opensearch.password:
            raise RuntimeError(
                "OPENSEARCH_USERNAME and OPENSEARCH_PASSWORD are required for basic auth"
            )
        return OpenSearch(
            hosts=[config.opensearch.node],
            http_auth=(config.opensearch.username, config.opensearch.password),
        )

    # Any other/unrecognized auth string (including "none") -- unauthenticated
    # client, exactly matching the TS fallthrough branch.
    return OpenSearch(hosts=[config.opensearch.node])
