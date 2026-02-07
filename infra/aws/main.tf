provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "dogcatcher-agent"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# Uncomment to use S3 backend for remote state:
# terraform {
#   backend "s3" {
#     bucket         = "dogcatcher-terraform-state"
#     key            = "dogcatcher-agent/terraform.tfstate"
#     region         = "eu-west-1"
#     dynamodb_table = "dogcatcher-terraform-locks"
#     encrypt        = true
#   }
# }
