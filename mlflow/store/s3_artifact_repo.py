import os

import posixpath
from six.moves import urllib

from mlflow import data
from mlflow.entities import FileInfo
from mlflow.exceptions import MlflowException
from mlflow.store.artifact_repo import ArtifactRepository
from mlflow.utils.file_utils import relative_path_to_artifact_path


class S3ArtifactRepository(ArtifactRepository):
    """Stores artifacts on Amazon S3."""
    @staticmethod
    def parse_s3_uri(uri):
        """Parse an S3 URI, returning (bucket, path)"""
        parsed = urllib.parse.urlparse(uri)
        if parsed.scheme != "s3":
            raise Exception("Not an S3 URI: %s" % uri)
        path = parsed.path
        if path.startswith('/'):
            path = path[1:]
        return parsed.netloc, path

    def _get_s3_client(self):
        import boto3
        s3_endpoint_url = os.environ.get('MLFLOW_S3_ENDPOINT_URL')
        return boto3.client('s3', endpoint_url=s3_endpoint_url)

    def log_artifact(self, local_file, artifact_path=None):
        (bucket, dest_path) = data.parse_s3_uri(self.artifact_uri)
        if artifact_path:
            dest_path = posixpath.join(dest_path, artifact_path)
        dest_path = posixpath.join(
            dest_path, os.path.basename(local_file))
        s3_client = self._get_s3_client()
        s3_client.upload_file(local_file, bucket, dest_path)

    def log_artifacts(self, local_dir, artifact_path=None):
        (bucket, dest_path) = data.parse_s3_uri(self.artifact_uri)
        if artifact_path:
            dest_path = posixpath.join(dest_path, artifact_path)
        s3_client = self._get_s3_client()
        local_dir = os.path.abspath(local_dir)
        for (root, _, filenames) in os.walk(local_dir):
            upload_path = dest_path
            if root != local_dir:
                rel_path = os.path.relpath(root, local_dir)
                rel_path = relative_path_to_artifact_path(rel_path)
                upload_path = posixpath.join(dest_path, rel_path)
            for f in filenames:
                s3_client.upload_file(
                        os.path.join(root, f),
                        bucket,
                        posixpath.join(upload_path, f))

    def list_artifacts(self, path=None):
        (bucket, artifact_path) = data.parse_s3_uri(self.artifact_uri)
        dest_path = artifact_path
        if path:
            dest_path = posixpath.join(dest_path, path)
        infos = []
        prefix = dest_path + "/"
        s3_client = self._get_s3_client()
        paginator = s3_client.get_paginator("list_objects_v2")
        results = paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter='/')
        for result in results:
            # Subdirectories will be listed as "common prefixes" due to the way we made the request
            for obj in result.get("CommonPrefixes", []):
                subdir_path = obj.get("Prefix")
                self._verify_listed_object_contains_artifact_path_prefix(
                    listed_object_path=subdir_path, artifact_path=artifact_path)
                subdir_rel_path = posixpath.relpath(
                    path=subdir_path, start=artifact_path)
                if subdir_rel_path.endswith("/"):
                    subdir_rel_path = subdir_rel_path[:-1]
                infos.append(FileInfo(subdir_rel_path, True, None))
            # Objects listed directly will be files
            for obj in result.get('Contents', []):
                file_path = obj.get("Key")
                if file_path.endswith('/'):
                    continue
                self._verify_listed_object_contains_artifact_path_prefix(
                    listed_object_path=file_path, artifact_path=artifact_path)
                file_rel_path = posixpath.relpath(path=file_path, start=artifact_path)
                file_size = int(obj.get('Size'))
                infos.append(FileInfo(file_rel_path, False, file_size))
        return sorted(infos, key=lambda f: f.path)

    @staticmethod
    def _verify_listed_object_contains_artifact_path_prefix(listed_object_path, artifact_path):
        if not listed_object_path.startswith(artifact_path):
            raise MlflowException(
                "The path of the listed S3 object does not begin with the specified"
                " artifact path. Artifact path: {artifact_path}. Object path:"
                " {object_path}.".format(
                    artifact_path=artifact_path, object_path=listed_object_path))

    def _download_file(self, remote_file_path, local_path):
        (bucket, s3_root_path) = data.parse_s3_uri(self.artifact_uri)
        s3_full_path = posixpath.join(s3_root_path, remote_file_path)
        s3_client = self._get_s3_client()
        s3_client.download_file(bucket, s3_full_path, local_path)
