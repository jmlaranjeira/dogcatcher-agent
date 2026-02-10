resource "aws_efs_file_system" "cache" {
  creation_token = "${var.project_name}-${var.environment}-cache"
  encrypted      = true

  tags = { Name = "${var.project_name}-cache" }
}

resource "aws_efs_mount_target" "cache" {
  count           = length(var.azs)
  file_system_id  = aws_efs_file_system.cache.id
  subnet_id       = aws_subnet.private[count.index].id
  security_groups = [aws_security_group.efs.id]
}

resource "aws_efs_access_point" "cache" {
  file_system_id = aws_efs_file_system.cache.id

  posix_user {
    uid = 10001
    gid = 10001
  }

  root_directory {
    path = "/agent-cache"

    creation_info {
      owner_uid   = 10001
      owner_gid   = 10001
      permissions = "755"
    }
  }

  tags = { Name = "${var.project_name}-cache-ap" }
}
