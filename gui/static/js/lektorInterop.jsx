import remote from 'remote';
import path from 'path';
import fs from 'fs';
import childProcess from 'child_process';

import i18n from './i18n';

const app = remote.require('app');
const dialog = remote.require('dialog');


function getResourceFolder() {
  // XXX: windows support
  return path.dirname(app.getAppPath());
}

function findBundledLektorExecutable() {
  let res = getResourceFolder();
  try {
    if (process.platform() === 'darwin') {
      let macExe = path.join(res, 'lektor');
      fs.accessSync(macExe, fs.X_OK);
      return macExe;
    }
  } catch (e) {
    return null;
  }
}

function findGlobalLektorExecutable() {
  let rv;
  if (process.platform === 'win32') {
    // XXX: do something better for windows support
    return 'lektor';
  } else {
    rv = childProcess.spawnSync('which', ['lektor']);
  }

  if (rv.status === 0) {
    return (rv.output[1] + '').trim();
  }
  return null;
}

export function findLektorExecutable() {
  return findBundledLektorExecutable() || findGlobalLektorExecutable();
}

class LektorServer {
  constructor(child, options) {
    this._child = child;
    this.state = 'starting';
    this.options = options;
    this._statusLineCallback = options.statusLineCallback;

    child.stdout.on('data', (data) => {
      var lines = (data + '').split(/\r?\n/);
      lines.forEach((line) => {
        this._statusLineCallback(line.trimRight());
      });
    });

    child.on('close', (code) => {
      this._statusLineCallback('Server shut down with code ' + code);
    });
  }

  getUrl() {
    return 'http://localhost:' + this.options.port + '/';
  }

  getAdminUrl() {
    return this.getUrl() + 'admin/';
  }

  shutdown() {
    this._child.kill('SIGHUP');
  }
}

function spawnLektor(exe, args) {
  var env = {};
  Object.keys(process.env).forEach((key) => {
    env[key] = process.env[key];
  });
  env.LEKTOR_RUN_FROM_UI = '1';
  env.LEKTOR_UI_LANG = i18n.currentLanguage;

  return childProcess.spawn(exe, args, {env: env});
}


export class LektorInterop {

  constructor() {
    this._lektorExecutable = null;
  }

  getLektorExecutable() {
    if (this._lektorExecutable !== null) {
      console.log('getLektorExecutable');
      return this._lektorExecutable;
    }
    return this._lektorExecutable = findLektorExecutable();
  }

  /* Loads lektor once to pre-initialize it (this will ensure that it
     unpacks the pex) and it also gives us back the version number. */
  checkLektor() {
    let exe = this.getLektorExecutable();
    return new Promise((resolve, reject) => {
      if (!exe) {
        return reject(new Error('Cannot locate Lektor executable'));
      }

      let child = spawnLektor(exe, ['--version']);
      let buf = '';
      child.stdout.on('data', (data) => {
        buf += data;
      });
      child.on('close', () => {
        let match = buf.match(/\s+version\s+(.*?)\s*$/i);
        if (match) {
          resolve(match[1]);
        } else {
          reject(new Error('Failed to launch Lektor executable'));
        }
      });
    });
  }

  /* given a path to a project this analyzes the project there and returns
     the data as object.  Since this loading can take time it's returned as
     a promise */
  analyzeProject(path) {
    let exe = this.getLektorExecutable();
    return new Promise((resolve, reject) => {
      if (!exe) {
        return reject(new Error('Cannot locate Lektor executable'));
      }

      let child = spawnLektor(
        exe, ['--project', path, 'project-info', '--json']);

      let buf = '';
      child.stdout.on('data', (data) => {
        buf += data;
      });

      child.on('close', (code) => {
        if (code === 0) {
          resolve(JSON.parse(buf));
        } else {
          resolve(null);
        }
      });
    });
  }

  /* spawns the server for a project */
  spawnServer(projectPath, options) {
    options.port = options.port || 5000;
    let exe = this.getLektorExecutable();
    let child = spawnLektor(
      exe, ['--project', projectPath, 'devserver', '--port',
            options.port + '']);
    return new LektorServer(child, options);
  }
}
