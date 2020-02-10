pipeline {
  agent any

  options {
    copyArtifactPermission(projectNames: 'blazar*')
  }

  stages {
    stage('test') {
      parallel {
        stage('pep8') {
          steps {
            sh 'source scl_source enable rh-python37 && tox -e pep8'
          }
        }
        stage('py37') {
          steps {
            sh 'source scl_source enable rh-python37 && tox -e py37'
          }
        }
      }
    }

    stage('package') {
      steps {
        dir('dist') {
          deleteDir()
        }
        sh 'python setup.py sdist'
        sh 'find dist -type f -exec cp {} dist/blazar.tar.gz \\;'
        archiveArtifacts(artifacts: 'dist/blazar.tar.gz', onlyIfSuccessful: true)
      }
    }
  }
}
