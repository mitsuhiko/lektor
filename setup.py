from setuptools import setup, find_packages


setup(
    name='Lektor',
    version='0.0.4.dev',
    url='http://github.com/mitsuhiko/lektor/',
    license='BSD',
    author='Armin Ronacher',
    author_email='armin.ronacher@active-4.com',
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    platforms='any',
    install_requires=[
        'Jinja2>=2.4',
        'click>=2.0',
        'watchdog',
        'Markdown',
        'Flask',
        'EXIFRead',
        'Pillow',
        'inifile',
    ],
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: Web Environment',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 3',
        'Topic :: Internet :: WWW/HTTP :: Dynamic Content',
        'Topic :: Software Development :: Libraries :: Python Modules'
    ],
    entry_points='''
        [console_scripts]
        lektor=lektor.cli:main
    '''
)
