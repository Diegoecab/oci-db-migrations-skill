"""
Database connector with auto-detection.

Tries three strategies in order:
  1. oracledb thin mode  (pure Python, no Oracle Client needed)
  2. oracledb thick mode  (requires Oracle Instant Client)
  3. sqlplus subprocess    (requires sqlplus in PATH)

Usage:
    connector = DBConnector.create(host, port, service_name, user, password)
    results = connector.execute("SELECT SYSDATE FROM DUAL")
    connector.close()
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Result types
# =============================================================================
@dataclass
class QueryResult:
    """Result of a SQL query execution."""
    columns: List[str] = field(default_factory=list)
    rows: List[List[Any]] = field(default_factory=list)
    error: Optional[str] = None
    row_count: int = 0

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def is_empty(self) -> bool:
        return self.row_count == 0

    def scalar(self) -> Any:
        """Return single value from first row, first column."""
        if self.rows and self.rows[0]:
            return self.rows[0][0]
        return None

    def column_values(self, col: int = 0) -> List[Any]:
        """Return all values from a specific column."""
        return [row[col] for row in self.rows if len(row) > col]

    def as_dicts(self) -> List[Dict[str, Any]]:
        """Return rows as list of dicts keyed by column names."""
        return [dict(zip(self.columns, row)) for row in self.rows]


# =============================================================================
# Abstract connector
# =============================================================================
class BaseConnector(ABC):
    """Abstract database connector."""

    def __init__(self, host: str, port: int, service_name: str,
                 user: str, password: str, **kwargs):
        self.host = host
        self.port = port
        self.service_name = service_name
        self.user = user
        self.password = password
        self.connect_as = kwargs.get("connect_as")  # e.g. "SYSDBA"
        self._connected = False

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection. Returns True on success."""
        pass

    @abstractmethod
    def execute(self, sql: str, params: Optional[dict] = None) -> QueryResult:
        """Execute SQL and return results."""
        pass

    @abstractmethod
    def execute_script(self, sql_script: str) -> List[QueryResult]:
        """Execute multi-statement SQL script."""
        pass

    @abstractmethod
    def close(self):
        """Close connection."""
        pass

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def connector_type(self) -> str:
        return self.__class__.__name__

    def dsn(self) -> str:
        return f"{self.host}:{self.port}/{self.service_name}"


# =============================================================================
# oracledb thin mode connector
# =============================================================================
class OracleDBThinConnector(BaseConnector):
    """Connector using python-oracledb in thin mode (pure Python)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._conn = None

    def connect(self) -> bool:
        try:
            import oracledb
            oracledb.init_oracle_client()  # no-op if already in thin mode
        except Exception:
            pass  # thin mode doesn't need client init

        try:
            import oracledb

            connect_kwargs = {
                "user": self.user,
                "password": self.password,
                "dsn": f"{self.host}:{self.port}/{self.service_name}",
            }
            if self.connect_as and self.connect_as.upper() == "SYSDBA":
                connect_kwargs["mode"] = oracledb.AUTH_MODE_SYSDBA

            self._conn = oracledb.connect(**connect_kwargs)
            self._connected = True
            logger.info(f"Connected via oracledb thin mode to {self.dsn()}")
            return True
        except Exception as e:
            logger.debug(f"oracledb thin mode failed: {e}")
            self._connected = False
            return False

    def execute(self, sql: str, params: Optional[dict] = None) -> QueryResult:
        if not self._conn:
            return QueryResult(error="Not connected")
        try:
            cursor = self._conn.cursor()
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)

            if cursor.description:
                columns = [col[0] for col in cursor.description]
                rows = [list(row) for row in cursor.fetchall()]
                return QueryResult(columns=columns, rows=rows, row_count=len(rows))
            else:
                self._conn.commit()
                return QueryResult(row_count=cursor.rowcount)
        except Exception as e:
            return QueryResult(error=str(e))

    def execute_script(self, sql_script: str) -> List[QueryResult]:
        results = []
        statements = [s.strip() for s in sql_script.split(";") if s.strip()]
        for stmt in statements:
            if stmt.upper().startswith("--"):
                continue
            results.append(self.execute(stmt))
        return results

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            self._connected = False


# =============================================================================
# oracledb thick mode connector
# =============================================================================
class OracleDBThickConnector(OracleDBThinConnector):
    """Connector using python-oracledb in thick mode (needs Instant Client)."""

    def connect(self) -> bool:
        try:
            import oracledb

            # Try to init Oracle Client for thick mode
            lib_dir = os.environ.get("ORACLE_HOME")
            if lib_dir:
                lib_dir = os.path.join(lib_dir, "lib")

            try:
                oracledb.init_oracle_client(lib_dir=lib_dir)
            except oracledb.ProgrammingError:
                pass  # Already initialized

            connect_kwargs = {
                "user": self.user,
                "password": self.password,
                "dsn": f"{self.host}:{self.port}/{self.service_name}",
            }
            if self.connect_as and self.connect_as.upper() == "SYSDBA":
                connect_kwargs["mode"] = oracledb.AUTH_MODE_SYSDBA

            self._conn = oracledb.connect(**connect_kwargs)
            self._connected = True
            logger.info(f"Connected via oracledb thick mode to {self.dsn()}")
            return True
        except Exception as e:
            logger.debug(f"oracledb thick mode failed: {e}")
            self._connected = False
            return False


# =============================================================================
# sqlplus subprocess connector
# =============================================================================
class SQLPlusConnector(BaseConnector):
    """Connector using sqlplus as subprocess (most compatible, least elegant)."""

    SQLPLUS_COMMANDS = ["sqlplus", "sql"]  # sqlplus or SQLcl

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sqlplus_cmd = None

    def _find_sqlplus(self) -> Optional[str]:
        """Find sqlplus or sql (SQLcl) in PATH."""
        for cmd in self.SQLPLUS_COMMANDS:
            if shutil.which(cmd):
                return cmd
        return None

    def connect(self) -> bool:
        self._sqlplus_cmd = self._find_sqlplus()
        if not self._sqlplus_cmd:
            logger.debug("Neither sqlplus nor sql (SQLcl) found in PATH")
            return False

        # Test connectivity with a trivial query
        result = self.execute("SELECT 'CONNECTED' FROM DUAL")
        if result.success and result.scalar() == "CONNECTED":
            self._connected = True
            logger.info(f"Connected via {self._sqlplus_cmd} to {self.dsn()}")
            return True
        else:
            logger.debug(f"sqlplus test failed: {result.error}")
            self._connected = False
            return False

    def _build_connect_string(self) -> str:
        """Build sqlplus connection string."""
        conn_str = f"{self.user}/{self.password}@{self.host}:{self.port}/{self.service_name}"
        if self.connect_as and self.connect_as.upper() == "SYSDBA":
            conn_str += " AS SYSDBA"
        return conn_str

    def execute(self, sql: str, params: Optional[dict] = None) -> QueryResult:
        if not self._sqlplus_cmd:
            return QueryResult(error="sqlplus not found")

        # Substitute params if provided (simple string replacement)
        if params:
            for key, val in params.items():
                sql = sql.replace(f":{key}", str(val))

        # Create temp SQL file
        script = (
            "SET PAGESIZE 0\n"
            "SET FEEDBACK OFF\n"
            "SET HEADING ON\n"
            "SET LINESIZE 32767\n"
            "SET COLSEP '|'\n"
            "SET TRIMSPOOL ON\n"
            "SET TRIMOUT ON\n"
            "SET TAB OFF\n"
            f"{sql.rstrip(';')};\n"
            "EXIT;\n"
        )

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
                f.write(script)
                tmp_path = f.name

            proc = subprocess.run(
                [self._sqlplus_cmd, "-S", self._build_connect_string()],
                stdin=open(tmp_path, "r"),
                capture_output=True, text=True, timeout=60
            )

            os.unlink(tmp_path)

            # Check for ORA- errors
            if proc.returncode != 0 or "ORA-" in proc.stdout or "SP2-" in proc.stdout:
                error_lines = [
                    l for l in proc.stdout.splitlines()
                    if l.startswith("ORA-") or l.startswith("SP2-")
                ]
                return QueryResult(error="\n".join(error_lines) or proc.stderr)

            return self._parse_output(proc.stdout)

        except subprocess.TimeoutExpired:
            return QueryResult(error="sqlplus command timed out (60s)")
        except Exception as e:
            return QueryResult(error=str(e))

    def _parse_output(self, raw: str) -> QueryResult:
        """Parse sqlplus pipe-delimited output into QueryResult."""
        lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
        if not lines:
            return QueryResult(row_count=0)

        # First non-empty line with '|' is the header (if HEADING ON)
        # Detect if first line contains separator pattern (dashes)
        header_idx = 0
        if len(lines) > 1 and re.match(r"^[-\s|]+$", lines[1]):
            # Has separator line → line 0 = header, line 1 = separator, rest = data
            columns = [c.strip() for c in lines[0].split("|")]
            data_start = 2
        elif "|" in lines[0]:
            columns = [c.strip() for c in lines[0].split("|")]
            data_start = 1
        else:
            # Single column, no header
            return QueryResult(
                columns=["VALUE"],
                rows=[[l] for l in lines],
                row_count=len(lines)
            )

        rows = []
        for line in lines[data_start:]:
            if line and not re.match(r"^[-\s|]+$", line):
                vals = [v.strip() for v in line.split("|")]
                rows.append(vals)

        return QueryResult(columns=columns, rows=rows, row_count=len(rows))

    def execute_script(self, sql_script: str) -> List[QueryResult]:
        """Execute full script as single sqlplus session."""
        if not self._sqlplus_cmd:
            return [QueryResult(error="sqlplus not found")]

        script = (
            "SET PAGESIZE 0\n"
            "SET FEEDBACK ON\n"
            "SET ECHO ON\n"
            "SET SERVEROUTPUT ON\n"
            "WHENEVER SQLERROR CONTINUE\n"
            f"{sql_script}\n"
            "EXIT;\n"
        )

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False) as f:
                f.write(script)
                tmp_path = f.name

            proc = subprocess.run(
                [self._sqlplus_cmd, "-S", self._build_connect_string()],
                stdin=open(tmp_path, "r"),
                capture_output=True, text=True, timeout=300
            )
            os.unlink(tmp_path)

            # Collect any ORA- errors
            errors = [l for l in proc.stdout.splitlines() if "ORA-" in l]
            if errors:
                return [QueryResult(error="\n".join(errors))]

            return [QueryResult(row_count=0)]  # Script executed

        except Exception as e:
            return [QueryResult(error=str(e))]

    def close(self):
        self._connected = False


# =============================================================================
# ADB Wallet Connector (for target ADB via wallet)
# =============================================================================
class ADBWalletConnector(BaseConnector):
    """Connector for ADB using downloaded wallet (oracledb thin + wallet)."""

    def __init__(self, *args, wallet_path: Optional[str] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._conn = None
        self.wallet_path = wallet_path

    def connect(self) -> bool:
        try:
            import oracledb

            if not self.wallet_path or not os.path.isdir(self.wallet_path):
                logger.debug(f"Wallet path not found: {self.wallet_path}")
                return False

            self._conn = oracledb.connect(
                user=self.user,
                password=self.password,
                dsn=self.service_name,  # TNS alias from tnsnames.ora in wallet
                config_dir=self.wallet_path,
                wallet_location=self.wallet_path,
                wallet_password=self.password,
            )
            self._connected = True
            logger.info(f"Connected via oracledb + wallet to ADB {self.service_name}")
            return True
        except Exception as e:
            logger.debug(f"ADB wallet connection failed: {e}")
            return False

    def execute(self, sql: str, params: Optional[dict] = None) -> QueryResult:
        # Delegates to same logic as OracleDBThinConnector
        if not self._conn:
            return QueryResult(error="Not connected")
        try:
            cursor = self._conn.cursor()
            cursor.execute(sql, params or {})
            if cursor.description:
                columns = [c[0] for c in cursor.description]
                rows = [list(r) for r in cursor.fetchall()]
                return QueryResult(columns=columns, rows=rows, row_count=len(rows))
            else:
                self._conn.commit()
                return QueryResult(row_count=cursor.rowcount)
        except Exception as e:
            return QueryResult(error=str(e))

    def execute_script(self, sql_script: str) -> List[QueryResult]:
        results = []
        for stmt in [s.strip() for s in sql_script.split(";") if s.strip()]:
            if not stmt.startswith("--"):
                results.append(self.execute(stmt))
        return results

    def close(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            self._connected = False


# =============================================================================
# Factory with auto-detect
# =============================================================================
class DBConnector:
    """
    Factory that auto-detects the best available connector.

    Order: oracledb thin → oracledb thick → sqlplus
    Override with preference param.
    """

    STRATEGIES = [
        ("oracledb_thin", OracleDBThinConnector),
        ("oracledb_thick", OracleDBThickConnector),
        ("sqlplus", SQLPlusConnector),
    ]

    @classmethod
    def create(
        cls,
        host: str,
        port: int,
        service_name: str,
        user: str,
        password: str,
        preference: str = "auto",
        **kwargs,
    ) -> BaseConnector:
        """
        Create and connect using best available strategy.

        Args:
            preference: "auto", "oracledb_thin", "oracledb_thick", or "sqlplus"
        """
        if preference != "auto":
            # Use specific strategy
            strategy_map = dict(cls.STRATEGIES)
            if preference not in strategy_map:
                raise ValueError(f"Unknown connector: {preference}. Options: {list(strategy_map.keys())}")
            connector = strategy_map[preference](host, port, service_name, user, password, **kwargs)
            if connector.connect():
                return connector
            raise ConnectionError(
                f"Failed to connect with {preference} to {host}:{port}/{service_name}"
            )

        # Auto-detect: try each strategy in order
        errors = []
        for name, connector_class in cls.STRATEGIES:
            logger.info(f"Trying {name}...")
            try:
                connector = connector_class(host, port, service_name, user, password, **kwargs)
                if connector.connect():
                    logger.info(f"Auto-detected connector: {name}")
                    return connector
            except Exception as e:
                errors.append(f"{name}: {e}")
                logger.debug(f"{name} failed: {e}")

        raise ConnectionError(
            f"All connectors failed for {host}:{port}/{service_name}.\n"
            + "\n".join(errors)
        )

    @classmethod
    def create_adb(
        cls,
        service_name: str,
        user: str,
        password: str,
        wallet_path: str,
        **kwargs,
    ) -> BaseConnector:
        """Create connector for ADB using wallet."""
        connector = ADBWalletConnector(
            host="", port=0, service_name=service_name,
            user=user, password=password,
            wallet_path=wallet_path, **kwargs,
        )
        if connector.connect():
            return connector
        raise ConnectionError(f"Failed to connect to ADB {service_name} via wallet at {wallet_path}")

    @classmethod
    def probe_available(cls) -> Dict[str, bool]:
        """Check which connector backends are available (without connecting)."""
        available = {}

        # oracledb
        try:
            import oracledb
            available["oracledb_thin"] = True
            # Check thick mode (Instant Client)
            try:
                oracledb.init_oracle_client()
                available["oracledb_thick"] = True
            except Exception:
                available["oracledb_thick"] = False
        except ImportError:
            available["oracledb_thin"] = False
            available["oracledb_thick"] = False

        # sqlplus
        available["sqlplus"] = shutil.which("sqlplus") is not None
        available["sqlcl"] = shutil.which("sql") is not None

        return available
