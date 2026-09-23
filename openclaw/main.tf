terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "ap-southeast-1"
}

data "aws_vpc" "default" {
  default = true
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-arm64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_security_group" "openclaw_sg" {
  name        = "openclaw-sg"
  description = "OpenClaw HTTP/HTTPS only, port 22 closed"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTPS inbound for Slack webhooks"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow all outbound traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "openclaw-sg"
  }
}

resource "aws_iam_role" "openclaw_role" {
  name = "openclaw-ec2-ssm-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_policy" {
  role       = aws_iam_role.openclaw_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "openclaw_profile" {
  name = "openclaw-ec2-instance-profile"
  role = aws_iam_role.openclaw_role.name
}

resource "aws_instance" "openclaw" {
  ami                  = data.aws_ami.ubuntu.id
  instance_type        = "t4g.small"
  iam_instance_profile = aws_iam_instance_profile.openclaw_profile.name
  security_groups      = [aws_security_group.openclaw_sg.name]

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 20
    delete_on_termination = true
  }

  tags = {
    Name = "openclaw-instance"
  }
}

output "instance_id" {
  value       = aws_instance.openclaw.id
  description = "EC2 Instance ID for SSM connection"
}
