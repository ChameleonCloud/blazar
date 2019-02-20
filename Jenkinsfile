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
            sh 'tox -e pep8'
          }
        }
        // stage('py36') {
        //   steps {
        //     sh 'tox -e py36'
        //   }
        // }
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
